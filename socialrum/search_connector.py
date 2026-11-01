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
DEFAULT_READABLE_TARGET = 25
MAX_READABLE_TARGET = 50
MAX_ATTEMPTS = 100
DEFAULT_MAX_PAGES = 10
MAX_PAGES = 10
DEFAULT_MAX_ELAPSED_SECONDS = 600
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
_ProgressCallback = Callable[[dict[str, int]], None]
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
      id: anchor.id || "",
      aria_label: anchor.getAttribute("aria-label") || "",
      anchor_text: (anchor.innerText || "").trim().slice(0, 500),
      result_text: (result.innerText || anchor.innerText || "").trim().slice(0, 1500)
    };
  });
})()'''

_VISIBLE_INSTAGRAM_ANCHOR_COUNT_PROBE = r'''(() => [...document.querySelectorAll("a[href]")]
  .filter((anchor) => anchor.getClientRects().length > 0)
  .filter((anchor) => {
    try {
      const url = new URL(anchor.href);
      return ["instagram.com", "www.instagram.com"].includes(url.hostname)
        && /^\/(p|reel|reels)\/[^/]+\/?$/.test(url.pathname);
    } catch (_) { return false; }
  }).length)()'''


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


def _wait_for_visible_search_results(target_id: str, runner: _Runner) -> bool:
    """Wait briefly for visible Instagram result anchors; return false on timeout."""
    command = [
        "cdp", "--browser-mode", "headed", "wait", "eval",
        _VISIBLE_INSTAGRAM_ANCHOR_COUNT_PROBE, "--ready-expr", "value >= 5",
        "--poll", "250ms", "--timeout", "4s", "--target", target_id, "--json",
    ]
    try:
        result = runner(list(command), capture_output=True, text=True,
                        timeout=CDP_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError as exc:
        raise CDPCommandError("The cdp CLI is required for public search") from exc
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CDPCommandError("The cdp readiness wait returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CDPCommandError("The cdp readiness wait returned invalid JSON")
    if result.returncode == 0 and payload.get("ok") is True:
        return True
    # A CLI timeout or conservative stop classification is handled by an
    # explicit stop-state check before the caller reads any visible results.
    wait_data = payload.get("data")
    wait_result = wait_data.get("wait") if isinstance(wait_data, dict) else None
    if (payload.get("code") in {"timeout", "stop_state"}
            or (isinstance(wait_result, dict) and wait_result.get("ready") is False)):
        return False
    raise CDPCommandError("The cdp readiness wait failed")


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
    if len(parts) != 2 or parts[0] not in {"p", "reel", "reels"} or not parts[1]:
        return None
    kind = "reel" if parts[0] == "reels" else parts[0]
    return urlunsplit(("https", "www.instagram.com", f"/{kind}/{parts[1]}/", "", ""))


def _next_page_url(payload: dict[str, Any], current_url: str) -> str | None:
    """Accept only a visible Google continuation that advances the same query."""
    value = payload.get("result", {}).get("value")
    if not isinstance(value, list):
        return None
    current = urlsplit(current_url)
    current_params = parse_qs(current.query)
    current_start = int((current_params.get("start") or ["0"])[0])
    query = (current_params.get("q") or [None])[0]
    for item in value:
        if not isinstance(item, dict):
            continue
        label = " ".join(str(item.get("aria_label") or "").split()).lower()
        text = " ".join(str(item.get("anchor_text") or "").split()).lower()
        if (item.get("id") != "pnnext" and label != "next"
                and text not in {"next", "next page", "›", "→"}):
            continue
        href = item.get("href")
        if not isinstance(href, str):
            continue
        candidate = urlsplit(href)
        params = parse_qs(candidate.query)
        try:
            next_start = int((params.get("start") or [""])[0])
        except ValueError:
            continue
        if (candidate.scheme == "https" and candidate.hostname in {"google.com", "www.google.com"}
                and candidate.path == "/search" and (params.get("q") or [None])[0] == query
                and params.get("tbs", []) == current_params.get("tbs", [])
                and next_start > current_start):
            return href
    return None


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


def _candidate_has_attempt(candidates: list[dict[str, Any]], before: int) -> bool:
    return any(
        item.get("status") in {"read", "unavailable", "unverified"}
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


def _mark_not_attempted(candidates: list[dict[str, Any]], index: int, reason: str) -> None:
    for remaining in candidates[index:]:
        if "status" not in remaining:
            remaining["status"] = "not_attempted"
            remaining["error"] = reason


def _stop_reason(exc: InstagramStopStateCheckError) -> str:
    if (isinstance(exc, InstagramAccessBoundaryError)
            and isinstance(exc.stop_state, str) and exc.stop_state in _SAFE_STOP_STATES):
        return exc.stop_state
    return "access_boundary"


def _emit_progress(callback: _ProgressCallback | None, candidates: list[dict[str, Any]],
                   target_readable: int, pages_visited: int) -> None:
    if callback is None:
        return
    snapshot = {
        "attempted_count": sum(
            item.get("status") in {"read", "unavailable", "unverified"}
            for item in candidates
        ),
        "readable_count": sum(
            item.get("status") == "read" and item.get("source_association") == "verified"
            and isinstance(item.get("post_text"), str) and bool(item["post_text"].strip())
            for item in candidates
        ),
        "target_readable": target_readable,
        "pages_visited": pages_visited,
    }
    try:
        callback(snapshot)
    except Exception:
        logger.debug("progress callback failed")


def search_and_collect_public_posts(
    keywords: str,
    *,
    runner: _Runner = subprocess.run,
    collector: _Collector = collect_visible_posts,
    max_candidates: int = MAX_CANDIDATES,
    target_readable: int | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_elapsed_seconds: int = DEFAULT_MAX_ELAPSED_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    progress_callback: _ProgressCallback | None = None,
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
            target_readable=target_readable,
            max_attempts=max_attempts,
            max_pages=max_pages,
            max_elapsed_seconds=max_elapsed_seconds,
            clock=clock,
            progress_callback=progress_callback,
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
    target_readable: int | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_elapsed_seconds: int = DEFAULT_MAX_ELAPSED_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    progress_callback: _ProgressCallback | None = None,
    reels_only: bool = False,
    recent_days: int | None = None,
) -> dict[str, Any]:
    """Search visible Google results and collect visible text from a bounded set of posts.

    The call is intended for an explicit user request only. Search snippets are
    returned as `search_result_text`; `post_text` is taken separately from the
    exact public Instagram permalink by `collect_visible_posts`. Per-post
    failures retain the search candidate and do not hide successful results.
    The one newly created browser tab is always closed by its exact target ID.
    The elapsed-time budget is cooperative and checked between CDP calls.
    """
    target_mode = target_readable is not None
    if target_mode:
        if (not isinstance(target_readable, int) or isinstance(target_readable, bool)
                or not 1 <= target_readable <= MAX_READABLE_TARGET):
            raise ValueError(f"target_readable must be between 1 and {MAX_READABLE_TARGET}")
        if (not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
                or not 1 <= max_attempts <= MAX_ATTEMPTS):
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}")
        if (not isinstance(max_pages, int) or isinstance(max_pages, bool)
                or not 1 <= max_pages <= MAX_PAGES):
            raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
        if (not isinstance(max_elapsed_seconds, (int, float)) or isinstance(max_elapsed_seconds, bool)
                or not 1 <= max_elapsed_seconds <= DEFAULT_MAX_ELAPSED_SECONDS):
            raise ValueError(f"max_elapsed_seconds must be between 1 and {DEFAULT_MAX_ELAPSED_SECONDS}")
        max_candidates = max_attempts
    candidate_limit = MAX_ATTEMPTS if target_mode else MAX_SEARCH_CANDIDATES
    if (
        not isinstance(max_candidates, int)
        or isinstance(max_candidates, bool)
        or not 1 <= max_candidates <= candidate_limit
    ):
        raise ValueError(f"max_candidates must be between 1 and {candidate_limit}")
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
    if not target_mode and max_candidates == MAX_SEARCH_CANDIDATES:
        search_params["num"] = str(MAX_SEARCH_CANDIDATES)
    search["url"] = "https://www.google.com/search?" + urlencode(search_params)
    run_id = uuid.uuid4().hex
    started = clock()
    search_started_at = datetime.now(timezone.utc)
    task_id = f"socialrum-search-{run_id}"
    target_id: str | None = None
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    stop_reason: str | None = None
    pages_visited = 0
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
        if target_mode:
            _wait_for_visible_search_results(target_id, runner)
            _require_clear_stop_state(target_id, runner)
        results = _run_json(["cdp", "--browser-mode", "headed", "eval", _VISIBLE_RESULTS_PROBE,
                             "--target", target_id, "--json"], runner)
        candidates = _candidate_rows(
            results, min(MAX_SEARCH_CANDIDATES, max_candidates), reels_only=reels_only
        )
        pages_visited = 1
        current_url = search["url"]
        seen_urls = {item["source_url"] for item in candidates}

        index = 0
        while index < (max_attempts if target_mode else len(candidates)):
            if target_mode and clock() - started >= max_elapsed_seconds:
                stop_reason = "time_ceiling"
                _mark_not_attempted(candidates, index, "Not attempted after elapsed-time ceiling.")
                break
            if target_mode and sum(
                item.get("status") == "read" and bool(str(item.get("post_text") or "").strip())
                and item.get("source_association") == "verified" for item in candidates
            ) >= target_readable:
                stop_reason = "target_reached"
                _mark_not_attempted(candidates, index, "Not attempted after readable target was reached.")
                break
            if target_mode and index >= len(candidates):
                if len(candidates) >= max_attempts:
                    stop_reason = "attempt_ceiling"
                    break
                if pages_visited >= max_pages:
                    stop_reason = "page_ceiling"
                    break
                next_url = _next_page_url(results, current_url)
                if next_url is None:
                    stop_reason = "no_more_results"
                    break
                try:
                    _run_json([
                        "cdp", "--browser-mode", "headed", "open", next_url,
                        "--new-tab=false", "--target", target_id, "--task-id", task_id,
                        "--root-task-id", task_id, "--run-id", run_id, "--json",
                    ], runner)
                    _run_json(["cdp", "--browser-mode", "headed", "wait", "load-state",
                               "domcontentloaded", "--target", target_id, "--json"], runner)
                    _require_clear_stop_state(target_id, runner)
                    _wait_for_visible_search_results(target_id, runner)
                    _require_clear_stop_state(target_id, runner)
                    results = _run_json(["cdp", "--browser-mode", "headed", "eval",
                                         _VISIBLE_RESULTS_PROBE, "--target", target_id, "--json"], runner)
                except InstagramStopStateCheckError as exc:
                    if not any(item.get("status") in {"read", "unavailable", "unverified"}
                               for item in candidates):
                        raise
                    stop_reason = _stop_reason(exc)
                    errors.append(f"Pagination stopped at access boundary ({stop_reason}).")
                    break
                except RuntimeError as exc:
                    stop_reason = "pagination_navigation_failed"
                    errors.append(f"Pagination stopped after navigation failure: {str(exc)[:200]}")
                    break
                current_url = next_url
                pages_visited += 1
                rows = _candidate_rows(
                    results, MAX_SEARCH_CANDIDATES,
                    reels_only=reels_only,
                )
                for row in rows:
                    if row["source_url"] not in seen_urls:
                        seen_urls.add(row["source_url"])
                        candidates.append(row)
                        if len(candidates) >= max_attempts:
                            break
                if index >= len(candidates):
                    continue
            candidate = candidates[index]
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
                _emit_progress(progress_callback if target_mode else None, candidates,
                               target_readable or 0, pages_visited)
                break

            # A challenge overlay can coexist with readable-looking page content.
            # Classify every destination before the collector inspects it.
            try:
                _require_clear_stop_state(target_id, runner)
            except InstagramStopStateCheckError as exc:
                prior_attempt = (_candidate_has_attempt(candidates, index) if target_mode
                                 else _candidate_has_success(candidates, index))
                if not prior_attempt:
                    raise
                stop_reason = _stop_reason(exc)
                _record_stopped_candidates(candidates, index, stop_reason, errors)
                _emit_progress(progress_callback if target_mode else None, candidates,
                               target_readable or 0, pages_visited)
                break
            try:
                if collector is collect_visible_posts:
                    if target_mode:
                        posts = collector(candidate["source_url"], runner=runner,
                                          target_id=target_id, include_metadata=False)
                    else:
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
                if target_mode:
                    collected_url = _canonical_post_url(posts[0].get("source_url"))
                    if (posts[0].get("source_association") != "verified"
                            or collected_url != candidate["source_url"]):
                        candidate["status"] = "unverified"
                        candidate["source_association"] = "unverified"
                        candidate["error"] = "Collector did not verify caption association to the requested reel."
                        index += 1
                        _emit_progress(progress_callback, candidates, target_readable or 0, pages_visited)
                        continue
                    candidate["source_association"] = "verified"
                if "posted_at" in posts[0]:
                    candidate["posted_at"] = posts[0]["posted_at"]
                creator = _valid_creator(posts[0].get("creator"))
                if creator is not None:
                    candidate["creator"] = creator
            except InstagramStopStateCheckError as exc:
                prior_attempt = (_candidate_has_attempt(candidates, index) if target_mode
                                 else _candidate_has_success(candidates, index))
                if not prior_attempt:
                    raise
                stop_reason = _stop_reason(exc)
                _record_stopped_candidates(candidates, index, stop_reason, errors)
                _emit_progress(progress_callback if target_mode else None, candidates,
                               target_readable or 0, pages_visited)
                break
            except (RuntimeError, ValueError, KeyError, IndexError) as exc:
                candidate["status"] = "unavailable"
                candidate["error"] = str(exc)[:300]
                errors.append(f"{candidate['source_url']}: {str(exc)[:300]}")
            index += 1
            if target_mode:
                _emit_progress(progress_callback, candidates, target_readable or 0, pages_visited)
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

    search_ended_at = datetime.now(timezone.utc)
    readable_count = sum(
        item.get("status") == "read" and isinstance(item.get("post_text"), str)
        and bool(item["post_text"].strip())
        and (not target_mode or item.get("source_association") == "verified")
        for item in candidates
    )
    elapsed_seconds = max(0.0, clock() - started)
    if target_mode and readable_count >= target_readable:
        stop_reason = "target_reached"
    elif target_mode and stop_reason is None:
        if elapsed_seconds >= max_elapsed_seconds:
            stop_reason = "time_ceiling"
        elif len(candidates) >= max_attempts:
            stop_reason = "attempt_ceiling"
        else:
            stop_reason = "no_more_results"
    return {
        "source_kind": "user_run_public_search_and_visible_posts",
        "search_engine": "Google",
        "query": search["query"],
        "index_time_filter": f"qdr:d{recent_days}" if recent_days is not None else None,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "started_at": search_started_at.isoformat(),
        "ended_at": search_ended_at.isoformat(),
        "candidates": candidates,
        "errors": errors,
        "stop_reason": stop_reason,
        "target_readable": target_readable,
        "attempted_count": sum(
            item.get("status") in {"read", "unavailable", "unverified"}
            for item in candidates
        ),
        "readable_count": readable_count,
        "elapsed_seconds": elapsed_seconds,
        "pages_visited": pages_visited,
        "time_budget_note": (
            "Elapsed-time limit is cooperative and checked between CDP operations; "
            "individual CDP operations have their own timeout, so a run can exceed the budget."
            if target_mode else None
        ),
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
