"""Read visible public Instagram posts from an already-open, user-supplied page.

The collector uses the local ``cdp`` CLI only. It does not navigate, click,
scroll, inspect storage/network data, or access login credentials. Instagram
may restrict automated access; use only pages and content the user is authorized
to view and respect the site's terms.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit, urlunsplit

MAX_POSTS = 50
MAX_POST_TEXT_CHARS = 4_000
CDP_TIMEOUT_SECONDS = 15
# Observed on one public Instagram post in the installed headed CDP target. The
# hashed classes are fragile; this selector is used only on exact /p/ or /reel/
# routes and must resolve uniquely. A failed/ambiguous match is reported as no
# usable result; it never broadens to a card, main region, or comments list.
_SINGLE_POST_TEXT_SELECTOR = (
    "span.x193iq5w.xeuugli.x13faqbe.x1vvkbs.xt0psk2.x1i0vuye."
    "xvs91rp.xo1l8bm.x5n08af.x126k92a"
)
_SINGLE_POST_DATE_PROBE = r'''(() => {
  const selector = "span.x193iq5w.xeuugli.x13faqbe.x1vvkbs.xt0psk2.x1i0vuye.xvs91rp.xo1l8bm.x5n08af.x126k92a";
  const visible = (element) => element.getClientRects().length > 0;
  const captions = [...document.querySelectorAll(selector)].filter(visible);
  if (captions.length !== 1) return {status: "caption_not_unique", caption_count: captions.length};
  let ancestor = captions[0];
  for (let hops = 1; hops <= 8; hops += 1) {
    ancestor = ancestor.parentElement;
    if (!ancestor) break;
    const captionCount = [...ancestor.querySelectorAll(selector)].filter(visible).length;
    const times = [...ancestor.querySelectorAll("time[datetime]")].filter(visible);
    if (times.length > 0) {
      const reserved = new Set(["about", "accounts", "ads", "api", "apple", "challenge", "developer", "direct", "directory", "explore", "legal", "popular", "privacy", "p", "reel", "reels", "stories", "tv", "web"]);
      const profilePaths = [...ancestor.querySelectorAll("a[href]")].filter(visible).map((anchor) => {
        try {
          const link = new URL(anchor.href, location.href);
          const parts = link.pathname.split("/").filter(Boolean);
          return link.origin === location.origin && parts.length === 1
            && /^[A-Za-z0-9._]{1,30}$/.test(parts[0])
            && !reserved.has(parts[0].toLowerCase()) ? parts[0] : null;
        } catch (_) { return null; }
      }).filter(Boolean);
      return {
        status: captionCount === 1 && times.length === 1 ? "unique" : "ambiguous",
        caption_count: captionCount,
        time_count: times.length,
        block_text_chars: (ancestor.textContent || "").length,
        datetime: times.length === 1 ? times[0].getAttribute("datetime") : null,
        creator_count: profilePaths.length,
        creator: profilePaths.length === 1 ? profilePaths[0] : null
      };
    }
  }
  return {status: "no_scoped_time", caption_count: 1};
})()'''
_OWNED_REELS_CAPTION_PROBE = r'''(() => {
  const selector = 'div[dir="auto"].xvs91rp.xd4r4e8';
  const visible = (element) => element.getClientRects().length > 0;
  const videos = [...document.querySelectorAll("video")].filter(visible);
  if (videos.length === 0) return {status: "no_visible_video"};
  const firstVideo = videos[0];
  const candidates = [...document.querySelectorAll(selector)].filter(visible);
  const matches = [];
  for (const caption of candidates) {
    let ancestor = caption.parentElement;
    for (let hops = 0; ancestor && hops < 24; hops += 1, ancestor = ancestor.parentElement) {
      if (!ancestor.contains(firstVideo)) continue;
      const cardVideos = [...ancestor.querySelectorAll("video")].filter(visible);
      const cardCaptions = [...ancestor.querySelectorAll(selector)].filter(visible);
      if (cardVideos.length === 1 && cardCaptions.length === 1) {
        matches.push(caption);
        break;
      }
    }
  }
  if (matches.length !== 1) {
    return {status: matches.length > 1 ? "ambiguous" : "no_unique_first_card_caption"};
  }
  const text = (matches[0].innerText || "").replace(/\s+/g, " ").trim();
  if (!text) return {status: "empty_caption"};
  return {status: "unique", text: text.slice(0, 4000)};
})()'''
_Runner = Callable[..., Any]
_CREATOR_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
_STOP_STATE_RE = re.compile(r"[a-z0-9_]{1,64}\Z")
_RESERVED_INSTAGRAM_PATHS = {
    "about", "accounts", "ads", "api", "apple", "challenge", "developer",
    "direct", "directory", "explore", "legal", "popular", "privacy", "p",
    "reel", "reels", "stories", "tv", "web",
}


class InstagramStopStateCheckError(RuntimeError):
    """Raised when a page's stop-state cannot be reliably cleared."""

    def __init__(
        self,
        message: str = (
            "The browser page could not be verified as accessible. "
            "Review it manually; no post content was collected."
        ),
    ) -> None:
        super().__init__(message)


class InstagramAccessBoundaryError(InstagramStopStateCheckError):
    """Raised when cdp confirms an access boundary on an Instagram page."""

    def __init__(self, stop_state: object = None) -> None:
        self.stop_state = (
            stop_state if isinstance(stop_state, str) and _STOP_STATE_RE.fullmatch(stop_state)
            else None
        )
        super().__init__("The Instagram page is at an access boundary; no content was collected")


def _instagram_url(url: str) -> tuple[str, str, str]:
    """Return canonical URL, host, and path after strict public URL checks."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Provide an exact public Instagram page URL")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Instagram URL is malformed") from exc
    host = (parsed.hostname or "").lower()
    if (parsed.scheme.lower() != "https" or not host
            or (host != "instagram.com" and not host.endswith(".instagram.com"))
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443)):
        raise ValueError("Only HTTPS Instagram page URLs are supported")
    path = parsed.path or "/"
    canonical = urlunsplit(("https", host, path, "", ""))
    return canonical, host, path


def _run_json(command: Sequence[str], runner: _Runner) -> dict[str, Any]:
    try:
        result = runner(
            list(command), capture_output=True, text=True, timeout=CDP_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("The cdp CLI is required for Instagram collection") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The cdp command timed out") from exc
    if result.returncode != 0:
        raise RuntimeError(f"The cdp command failed (exit {result.returncode})")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("The cdp command returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("The cdp command did not return a successful result")
    return payload


def _visible_texts(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("The cdp snapshot did not contain visible text items")
    visible: list[str] = []
    for item in items[:MAX_POSTS]:
        value = item if isinstance(item, str) else item.get("text") if isinstance(item, dict) else None
        if isinstance(value, str):
            text = " ".join(value.split())[:MAX_POST_TEXT_CHARS]
            if text:
                visible.append(text)
    return visible


def _is_expected_tag_redirect(requested_path: str, page_path: str) -> bool:
    """Recognize Instagram's same-site canonicalization of a tag route."""
    requested_parts = requested_path.strip("/").split("/")
    page_parts = page_path.strip("/").split("/")
    return (
        len(requested_parts) == 3 and requested_parts[:2] == ["explore", "tags"]
        and len(page_parts) == 2 and page_parts[0] == "popular"
        and page_parts[1] == requested_parts[2]
    )


def _is_single_post_path(path: str) -> bool:
    parts = path.strip("/").split("/")
    return len(parts) == 2 and parts[0] in {"p", "reel"} and bool(parts[1])


def _owned_target_path_matches(requested_path: str, observed_path: str) -> bool:
    """Accept Instagram's same-shortcode /reel/ to /reels/ canonicalization."""
    if requested_path == observed_path:
        return True
    requested_parts = requested_path.strip("/").split("/")
    observed_parts = observed_path.strip("/").split("/")
    return (
        len(requested_parts) == len(observed_parts) == 2
        and requested_parts[0] in {"reel", "reels"}
        and observed_parts[0] in {"reel", "reels"}
        and requested_parts[1] == observed_parts[1]
    )


def _is_owned_reels_path(path: str) -> bool:
    parts = path.strip("/").split("/")
    return len(parts) == 2 and parts[0] == "reels" and bool(parts[1])


def _owned_reels_caption(target_id: str, runner: _Runner) -> str:
    """Read one visible caption only when uniquely tied to the first reel video card."""
    result = _run_json([
        "cdp", "--browser-mode", "headed", "eval", _OWNED_REELS_CAPTION_PROBE,
        "--target", target_id, "--json",
    ], runner)
    evaluation = result.get("result")
    value = evaluation.get("value") if isinstance(evaluation, dict) else None
    if not isinstance(value, dict):
        raise RuntimeError("The visible reel caption probe returned an invalid result")
    if value.get("status") == "ambiguous":
        raise RuntimeError("The first reel video card had an ambiguous visible caption")
    text = value.get("text")
    if value.get("status") != "unique" or not isinstance(text, str) or not text.strip():
        raise RuntimeError("No unique visible caption was available in the first reel video card")
    return " ".join(text.split())[:MAX_POST_TEXT_CHARS]


def _single_post_metadata(target_id: str, runner: _Runner) -> tuple[str | None, str | None]:
    """Return date and creator only from one compact, uniquely scoped caption block."""
    try:
        result = _run_json([
            "cdp", "--browser-mode", "headed", "eval", _SINGLE_POST_DATE_PROBE,
            "--target", target_id, "--json",
        ], runner)
    except RuntimeError:
        return None, None
    evaluation = result.get("result")
    if not isinstance(evaluation, dict):
        return None, None
    value = evaluation.get("value")
    if (not isinstance(value, dict) or value.get("status") != "unique"
            or value.get("caption_count") != 1 or value.get("time_count") != 1
            or not isinstance(value.get("block_text_chars"), int)
            or not 1 <= value["block_text_chars"] <= 1_000):
        return None, None
    creator = value.get("creator")
    if (value.get("creator_count") != 1 or not isinstance(creator, str)
            or not _CREATOR_HANDLE_RE.fullmatch(creator)
            or creator.lower() in _RESERVED_INSTAGRAM_PATHS):
        creator = None
    raw = value.get("datetime")
    if not isinstance(raw, str):
        return None, creator
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, creator
    if parsed.tzinfo is None:
        return None, creator
    return parsed.date().isoformat(), creator


def _require_clear_stop_state(target_id: str, runner: _Runner) -> None:
    """Stop before content extraction unless cdp clears this exact target."""
    try:
        result = _run_json([
            "cdp", "--browser-mode", "headed", "stop-state", "classify",
            "--target", target_id, "--json",
        ], runner)
    except RuntimeError as exc:
        raise InstagramStopStateCheckError from exc
    target = result.get("target")
    if (result.get("classification_ok") is not True
            or not isinstance(target, dict) or target.get("id") != target_id):
        raise InstagramStopStateCheckError
    for field in ("agent_should_stop", "human_required"):
        if field in result and type(result[field]) is not bool:
            raise InstagramStopStateCheckError
    if "stop_state" in result and not isinstance(result["stop_state"], str):
        raise InstagramStopStateCheckError
    stop_state = result.get("stop_state")
    if (result.get("status") == "blocked" or result.get("agent_should_stop") is True
            or result.get("human_required") is True or bool(stop_state)):
        raise InstagramAccessBoundaryError(stop_state)
    if result.get("status") != "ok":
        raise InstagramStopStateCheckError


def collect_visible_posts(
    url: str, *, runner: _Runner = subprocess.run, target_id: str | None = None,
) -> list[dict[str, str]]:
    """Collect visible post text from an exact, already-open page.

    The URL must be supplied by the user. The collector only inspects an
    existing headed tab whose URL has the same Instagram host and path; when
    ``target_id`` is supplied, it selects that exact target and validates its
    URL instead of requiring a globally unique URL match. It never opens or
    navigates a tab. Tag pages use visible ``article`` items;
    exact single-post/reel pages use one narrowly observed caption span and
    fail if it is empty or ambiguous. Returns bounded ``source_url``/``text``
    records. Raises ``ValueError`` for an invalid URL and ``RuntimeError`` for
    CDP failures, missing matching tabs, inaccessible content, or ambiguous text.
    """
    _, host, path = _instagram_url(url)
    owned_target = target_id is not None
    pages = _run_json(["cdp", "--browser-mode", "headed", "pages", "--json"], runner)
    targets = pages.get("pages")
    if not isinstance(targets, list):
        raise RuntimeError("The cdp pages result did not contain page targets")

    if target_id is not None:
        matches = [
            page for page in targets
            if isinstance(page, dict) and page.get("type") == "page" and page.get("id") == target_id
        ]
        if len(matches) != 1:
            raise RuntimeError("The owned headed tab could not be identified for collection")
        page = matches[0]
        try:
            _, page_host, page_path = _instagram_url(page.get("url", ""))
        except ValueError as exc:
            raise RuntimeError("The owned headed tab is not showing the requested Instagram URL") from exc
        if page_host != host or not (
            _owned_target_path_matches(path, page_path)
            or _is_expected_tag_redirect(path, page_path)
        ):
            raise RuntimeError("The owned headed tab is not showing the requested Instagram URL")
    else:
        matches = []
        for page in targets:
            if not isinstance(page, dict) or page.get("type") != "page":
                continue
            try:
                _, page_host, page_path = _instagram_url(page.get("url", ""))
            except ValueError:
                continue
            if page_host == host and (page_path == path or _is_expected_tag_redirect(path, page_path)):
                matches.append((page, page_host, page_path))
        if len(matches) != 1:
            raise RuntimeError("Open the exact public Instagram URL in one headed tab before collecting")
        page, page_host, page_path = matches[0]
        target_id = page.get("id")
        if not isinstance(target_id, str) or not target_id:
            raise RuntimeError("The matching Instagram page has no CDP target ID")

    _require_clear_stop_state(target_id, runner)

    is_single_post = _is_single_post_path(path) or (
        owned_target and _is_owned_reels_path(page_path)
    )
    used_reels_fallback = False
    if is_single_post:
        result = _run_json([
            "cdp", "--browser-mode", "headed", "text", _SINGLE_POST_TEXT_SELECTOR,
            "--target", target_id, "--limit", "2", "--json",
        ], runner)
        texts = _visible_texts(result)
        if not texts and owned_target and _is_owned_reels_path(page_path):
            texts = [_owned_reels_caption(target_id, runner)]
            used_reels_fallback = True
        if len(texts) != 1:
            # Do not broaden selectors or inspect hidden app state at access walls.
            message = (
                "The visible post caption is ambiguous" if texts else
                "No visible post caption was available; the page may require login or be unavailable"
            )
            raise RuntimeError(message)
        posted_at, creator = (None, None) if used_reels_fallback else _single_post_metadata(target_id, runner)
    else:
        snapshot = _run_json([
            "cdp", "--browser-mode", "headed", "snapshot", "--target", target_id,
            "--selector", "article", "--limit", str(MAX_POSTS), "--json",
        ], runner)
        texts = _visible_texts(snapshot)
        if not texts:
            # Do not inspect hidden app state or attempt to bypass an access wall.
            raise RuntimeError("No visible Instagram articles were available; the page may require login or be unavailable")
    source_url = urlunsplit(("https", page_host, page_path, "", ""))
    records = [{"source_url": source_url, "text": text} for text in texts]
    if is_single_post and posted_at:
        records[0]["posted_at"] = posted_at
    if is_single_post and creator:
        records[0]["creator"] = creator
    return records
