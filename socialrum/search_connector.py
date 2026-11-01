"""User-initiated, best-effort public Instagram search via visible Google results.

This uses the locally installed ``cdp`` CLI in the user's headed browser. It
reads visible search-result anchors and, for a small bounded set, asks the
existing Instagram collector to read visible post text. It does not inspect
hidden page state, network responses, cookies, or credentials, and does not
work around access controls. It does not verify whether the browser is signed
in or whether a visible post is publicly accessible; the user must ensure both
public access and appropriate authorization. Automated collection may be
restricted by the sites' terms.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from socialrum.discovery import build_instagram_search
from socialrum.instagram import (
    InstagramAccessBoundaryError,
    InstagramStopStateCheckError,
    collect_visible_posts,
)

MAX_CANDIDATES = 5
MAX_SEARCH_CANDIDATES = 20
MAX_RESULT_TEXT_CHARS = 1_500
CDP_TIMEOUT_SECONDS = 20
_CREATOR_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
_RESERVED_INSTAGRAM_PATHS = {
    "about", "accounts", "ads", "api", "apple", "challenge", "developer",
    "direct", "directory", "explore", "legal", "popular", "privacy", "p",
    "reel", "reels", "stories", "tv", "web",
}
_SAFE_STOP_STATES = {
    "access_denied",
    "human_required",
    "login_required",
    "permission_required",
    "unusual_traffic",
}
_Runner = Callable[..., Any]
_Collector = Callable[..., list[dict[str, str]]]
_RUN_LOCK = Lock()
logger = logging.getLogger(__name__)


# This expression reads only visible anchor text and visible ancestor text.
_VISIBLE_RESULTS_PROBE = r'''(() => {
  const visible = (element) => element.getClientRects().length > 0;
  const anchors = [...document.querySelectorAll("a[href]")].filter(visible);
  return anchors.map((anchor) => {
    let result = anchor;
    for (let hops = 0; hops < 5 && result.parentElement; hops += 1) {
      result = result.parentElement;
      if ((result.innerText || "").trim().length >= (anchor.innerText || "").trim().length + 20) break;
    }
    return {
      href: anchor.href,
      anchor_text: (anchor.innerText || "").trim().slice(0, 500),
      result_text: (result.innerText || anchor.innerText || "").trim().slice(0, 1500)
    };
  });
})()'''


class CDPCommandError(RuntimeError):
    """A sanitized CDP failure with a coarse outcome for safe recovery logic."""

    def __init__(self, message: str, *, outcome: str = "failure"):
        self.outcome = outcome
        super().__init__(message)


def _run_json(command: Sequence[str], runner: _Runner) -> dict[str, Any]:
    started = time.monotonic()
    phase = _cdp_phase(command)
    outcome = "failure"
    try:
        try:
            logger.debug("cdp_phase phase=%s outcome=started", phase)
            result = runner(
                list(command), capture_output=True, text=True,
                timeout=CDP_TIMEOUT_SECONDS, check=False,
            )
        except FileNotFoundError as exc:
            raise CDPCommandError("The cdp CLI is required for public search") from exc
        except subprocess.TimeoutExpired as exc:
            outcome = "timeout"
            raise CDPCommandError("The cdp command timed out", outcome="timeout") from exc
        if result.returncode != 0:
            raise CDPCommandError(f"The cdp command failed (exit {result.returncode})")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CDPCommandError("The cdp command returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise CDPCommandError("The cdp command did not return a successful result")
        outcome = "success"
        return payload
    finally:
        elapsed_ms = round((time.monotonic() - started) * 1_000)
        logger.debug("cdp_phase phase=%s elapsed_ms=%d outcome=%s", phase, elapsed_ms, outcome)


def _cdp_phase(command: Sequence[str]) -> str:
    """Classify the operation using command tokens without retaining arguments."""
    tokens = set(command)
    if "stop-state" in tokens:
        return "stop-state"
    if "page" in tokens and "close" in tokens:
        return "close"
    for phase in ("open", "wait", "eval"):
        if phase in tokens:
            return phase
    return "other"


def _canonical_post_url(value: Any) -> str | None:
    """Accept only public Instagram post/reel permalinks (or Google redirect URLs)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.hostname and parsed.hostname.lower() in {"google.com", "www.google.com"}:
            redirect = parse_qs(parsed.query).get("q", [None])[0]
            if redirect:
                value = redirect
                parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in {"instagram.com", "www.instagram.com"}
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] not in {"p", "reel"} or not parts[1]:
        return None
    return urlunsplit(("https", "www.instagram.com", f"/{parts[0]}/{parts[1]}/", "", ""))


def _candidate_rows(payload: dict[str, Any], limit: int, *, reels_only: bool = False) -> list[dict[str, str]]:
    value = payload.get("result", {}).get("value")
    if not isinstance(value, list):
        raise RuntimeError("The visible search result probe returned an invalid result")
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _canonical_post_url(item.get("href"))
        if reels_only and url and urlsplit(url).path.split("/")[1] != "reel":
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        result_text = " ".join(str(item.get("result_text") or item.get("anchor_text") or "").split())
        candidates.append({
            "source_url": url,
            "search_result_text": result_text[:MAX_RESULT_TEXT_CHARS],
        })
        if len(candidates) >= limit:
            break
    return candidates


def _valid_creator(value: Any) -> str | None:
    """Accept only a collector-validated Instagram username when present."""
    if (
        not isinstance(value, str)
        or not _CREATOR_HANDLE_RE.fullmatch(value)
        or value.lower() in _RESERVED_INSTAGRAM_PATHS
    ):
        return None
    return value


def _require_clear_stop_state(target_id: str, runner: _Runner) -> None:
    try:
        state = _run_json([
            "cdp", "--browser-mode", "headed", "stop-state", "classify",
            "--target", target_id, "--json",
        ], runner)
    except RuntimeError as exc:
        raise InstagramStopStateCheckError from exc
    target = state.get("target")
    if (state.get("classification_ok") is not True
            or not isinstance(target, dict) or target.get("id") != target_id):
        raise InstagramStopStateCheckError
    for flag in ("agent_should_stop", "human_required"):
        if flag in state and not isinstance(state[flag], bool):
            raise InstagramStopStateCheckError
    stop_state = state.get("stop_state")
    if "stop_state" in state and not isinstance(stop_state, str):
        raise InstagramStopStateCheckError
    if (state.get("status") == "blocked" or state.get("agent_should_stop") is True
            or state.get("human_required") is True or bool(stop_state)):
        raise InstagramAccessBoundaryError(stop_state)
    if state.get("status") != "ok":
        raise InstagramStopStateCheckError


def _candidate_has_success(candidates: list[dict[str, Any]], before: int) -> bool:
    return any(
        item.get("status") == "read"
        and isinstance(item.get("post_text"), str)
        and item["post_text"].strip()
        for item in candidates[:before]
    )


def _record_stopped_candidates(
    candidates: list[dict[str, Any]], index: int, reason: str, errors: list[str],
) -> None:
    candidate = candidates[index]
    candidate["status"] = "unavailable"
    candidate["error"] = f"Stopped at access boundary ({reason})"
    errors.append(
        f"Collection stopped at access boundary ({reason}); remaining candidates were not attempted."
    )
    for remaining in candidates[index + 1:]:
        remaining["status"] = "not_attempted"
        remaining["error"] = "Not attempted after collection stopped at an access boundary."


def _stop_reason(exc: InstagramStopStateCheckError) -> str:
    if (isinstance(exc, InstagramAccessBoundaryError)
            and isinstance(exc.stop_state, str) and exc.stop_state in _SAFE_STOP_STATES):
        return exc.stop_state
    return "access_boundary"


def search_and_collect_public_posts(
    keywords: str,
    *,
    runner: _Runner = subprocess.run,
    collector: _Collector = collect_visible_posts,
    max_candidates: int = MAX_CANDIDATES,
    reels_only: bool = False,
    recent_days: int | None = None,
) -> dict[str, Any]:
    """Run one user-triggered connector at a time without queuing requests."""
    logger.debug("cdp_phase phase=run outcome=requested")
    if not _RUN_LOCK.acquire(blocking=False):
        logger.debug("cdp_phase phase=run outcome=busy")
        raise RuntimeError("Another public search is already running; wait for it to finish.")
    logger.debug("cdp_phase phase=run outcome=started")
    try:
        return _search_and_collect_public_posts_unlocked(
            keywords,
            runner=runner,
            collector=collector,
            max_candidates=max_candidates,
            reels_only=reels_only,
            recent_days=recent_days,
        )
    finally:
        _RUN_LOCK.release()


def _search_and_collect_public_posts_unlocked(
    keywords: str,
    *,
    runner: _Runner,
    collector: _Collector,
    max_candidates: int,
    reels_only: bool = False,
    recent_days: int | None = None,
) -> dict[str, Any]:
    """Search visible Google results and collect visible text from a bounded set of posts.

    The call is intended for an explicit user request only. Search snippets are
    returned as `search_result_text`; `post_text` is taken separately from the
    exact public Instagram permalink by `collect_visible_posts`. Per-post
    failures retain the search candidate and do not hide successful results.
    The one newly created browser tab is always closed by its exact target ID.
    """
    if (
        not isinstance(max_candidates, int)
        or isinstance(max_candidates, bool)
        or not 1 <= max_candidates <= MAX_SEARCH_CANDIDATES
    ):
        raise ValueError(f"max_candidates must be between 1 and {MAX_SEARCH_CANDIDATES}")
    if (recent_days is not None and (
            not isinstance(recent_days, int) or isinstance(recent_days, bool)
            or not 1 <= recent_days <= 365)):
        raise ValueError("recent_days must be between 1 and 365")
    search = build_instagram_search(keywords)
    if reels_only:
        search["query"] = search["query"].replace(
            "(site:instagram.com/p/ OR site:instagram.com/reel/)", "site:instagram.com/reel/"
        )
    search_params = {"q": search["query"]}
    if recent_days is not None:
        search_params["tbs"] = f"qdr:d{recent_days}"
    if max_candidates == MAX_SEARCH_CANDIDATES:
        search_params["num"] = str(MAX_SEARCH_CANDIDATES)
    search["url"] = "https://www.google.com/search?" + urlencode(search_params)
    run_id = uuid.uuid4().hex
    task_id = f"socialrum-search-{run_id}"
    target_id: str | None = None
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    stop_reason: str | None = None
    try:
        opened = _run_json([
            "cdp", "--browser-mode", "headed", "open", search["url"], "--new-tab",
            "--task-id", task_id, "--root-task-id", task_id, "--run-id", run_id, "--json",
        ], runner)
        page = opened.get("page")
        target_id = page.get("id") if isinstance(page, dict) else None
        if not isinstance(target_id, str) or not target_id:
            raise RuntimeError("The cdp open result did not include the created target ID")

        _run_json(["cdp", "--browser-mode", "headed", "wait", "load-state", "domcontentloaded",
                   "--target", target_id, "--json"], runner)
        _require_clear_stop_state(target_id, runner)
        results = _run_json(["cdp", "--browser-mode", "headed", "eval", _VISIBLE_RESULTS_PROBE,
                             "--target", target_id, "--json"], runner)
        candidates = _candidate_rows(results, max_candidates, reels_only=reels_only)

        for index, candidate in enumerate(candidates):
            try:
                _run_json([
                    "cdp", "--browser-mode", "headed", "open", candidate["source_url"],
                    "--new-tab=false", "--target", target_id, "--task-id", task_id,
                    "--root-task-id", task_id, "--run-id", run_id, "--json",
                ], runner)
                _run_json([
                    "cdp", "--browser-mode", "headed", "wait", "load-state", "domcontentloaded",
                    "--target", target_id, "--json",
                ], runner)
            except RuntimeError as exc:
                timed_out = isinstance(exc, CDPCommandError) and exc.outcome == "timeout"
                stop_reason = "candidate_navigation_timeout" if timed_out else "candidate_navigation_failed"
                cause = (
                    "Candidate navigation timed out"
                    if timed_out else "Candidate navigation failed or its outcome could not be confirmed"
                )
                candidate["status"] = "unavailable"
                candidate["error"] = f"{cause}; remaining candidates were not attempted."
                errors.append(f"{cause}; remaining candidates were not attempted.")
                for remaining in candidates[index + 1:]:
                    remaining["status"] = "not_attempted"
                    remaining["error"] = "Not attempted after candidate navigation failed."
                break

            # A challenge overlay can coexist with readable-looking page content.
            # Classify every destination before the collector inspects it.
            try:
                _require_clear_stop_state(target_id, runner)
            except InstagramStopStateCheckError as exc:
                if not _candidate_has_success(candidates, index):
                    raise
                stop_reason = _stop_reason(exc)
                _record_stopped_candidates(candidates, index, stop_reason, errors)
                break
            try:
                if collector is collect_visible_posts:
                    posts = collector(candidate["source_url"], runner=runner, target_id=target_id)
                else:
                    # Keep the existing injected collector contract intact.
                    posts = collector(candidate["source_url"], runner=runner)
                if (not isinstance(posts, list) or len(posts) != 1
                        or not isinstance(posts[0], dict)
                        or not isinstance(posts[0].get("text"), str)
                        or not posts[0]["text"].strip()):
                    raise RuntimeError(
                        "The collector did not return exactly one nonempty visible post text"
                    )
                candidate["status"] = "read"
                candidate["post_text"] = posts[0]["text"]
                if "posted_at" in posts[0]:
                    candidate["posted_at"] = posts[0]["posted_at"]
                creator = _valid_creator(posts[0].get("creator"))
                if creator is not None:
                    candidate["creator"] = creator
            except InstagramStopStateCheckError as exc:
                if not _candidate_has_success(candidates, index):
                    raise
                stop_reason = _stop_reason(exc)
                _record_stopped_candidates(candidates, index, stop_reason, errors)
                break
            except (RuntimeError, ValueError, KeyError, IndexError) as exc:
                candidate["status"] = "unavailable"
                candidate["error"] = str(exc)[:300]
                errors.append(f"{candidate['source_url']}: {str(exc)[:300]}")
    finally:
        if target_id:
            try:
                cleanup = _run_json(
                    ["cdp", "--browser-mode", "headed", "page", "close", "--target", target_id,
                     "--wait-gone", "--json"],
                    runner,
                )
                if cleanup.get("target_gone") is not True:
                    errors.append("Browser tab cleanup could not be confirmed for the owned target")
            except RuntimeError as exc:
                # Report cleanup failure in the returned result when possible.
                errors.append(f"Browser tab cleanup failed: {str(exc)[:200]}")

    return {
        "source_kind": "user_run_public_search_and_visible_posts",
        "search_engine": "Google",
        "query": search["query"],
        "index_time_filter": f"qdr:d{recent_days}" if recent_days is not None else None,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates,
        "errors": errors,
        "stop_reason": stop_reason,
        "provenance_note": (
            "Candidates came from visible Google search results, not Instagram's live search. "
            "Google may omit, rank, or retain pages unevenly; Google does not guarantee crawl, index, "
            "or serving, and changes may take days or longer to appear "
            "(https://developers.google.com/search/docs/fundamentals/how-search-works; "
            "https://developers.google.com/search/docs/crawling-indexing/troubleshoot-crawling-errors). "
            "Search result text is a Google snippet and may differ from the post. Post text is only "
            "what the existing visible-page collector could read at the exact public permalink. "
            "The optional Google time filter is a discovery/index filter, not proof of publication date; "
            "publication recency is unknown unless an observed post date is included for a candidate. "
            "The connector does not verify the browser's sign-in state or establish that a post is "
            "publicly accessible; the user must ensure public access and authorization. "
            "No freshness, completeness, popularity, representativeness, or trend claim is made. "
            "Instagram's Terms prohibit automated access or collection without express permission "
            "(https://help.instagram.com/581066165581870); use this feature only when authorized. "
            "Access walls and failures are not bypassed."
        ),
    }
