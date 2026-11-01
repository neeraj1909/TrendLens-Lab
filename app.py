"""Chainlit interface for the local SocialRum-inspired learning prototype."""

from __future__ import annotations

import logging
import re
import uuid
import asyncio
import os
import tempfile
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import chainlit as cl
from chainlit.sidebar import ElementSidebar

from socialrum.data import analyze, analyze_temporal_candidates, load_csv
from socialrum.drafts import generate_draft
from socialrum.discovery import build_instagram_search, normalize_instagram_phrase
from socialrum.instagram import collect_visible_posts
from socialrum.keyword_insights import analyze_keyword_insights
from socialrum.search_connector import search_and_collect_public_posts
from socialrum.snapshots import (
    compare_latest_snapshots,
    delete_snapshots,
    save_snapshot,
)
from socialrum.topic_signals import analyze_topic_signals
from socialrum.video import burn_captions
from socialrum.transcribe import TranscriptionError, transcribe_media
from socialrum.store import DraftConflictError, export_markdown, list_drafts, load_draft, save_draft

logger = logging.getLogger(__name__)
SAMPLE_CSV = Path(__file__).resolve().parent / "data" / "sample.csv"
DRAFT_STORE = Path.home() / ".local" / "share" / "socialrum-lab" / "drafts"
SNAPSHOT_STORE = Path.home() / ".local" / "share" / "socialrum-lab" / "search-snapshots"
SNAPSHOT_SOURCE = "Google visible search results"
SNAPSHOT_SOURCE_VERSION = "socialrum-search-connector-v1"
_INSTAGRAM_CREATOR_RE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
_INSTAGRAM_RESERVED_PATHS = {
    "about", "accounts", "ads", "api", "apple", "challenge", "developer",
    "direct", "directory", "explore", "legal", "popular", "privacy", "p",
    "reel", "reels", "stories", "tv", "web",
}
_PUBLIC_SEARCH_ADMISSION = threading.Lock()
_PUBLIC_SEARCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="socialrum-public-search"
)
INSTAGRAM_KEYWORD_PROMPT = "Enter one hashtag (for example, #finance) or a phrase (for example, personal finance). The app will search visible Google results and try to read up to five public post/reel pages. Use only public content you are authorized to access; the app does not verify authorization or guarantee the headed browser is logged out."
REEL_COLLECTION_PROMPT = "Enter one hashtag (for example, #finance) or a keyword phrase (for example, personal finance). This starts a separate bounded collection targeting 25 readable reels by default; results may be fewer."
PUBLIC_SEARCH_GENERATION_KEY = "socialrum_public_search_generation"
WELCOME_MESSAGE = (
    "# SocialRum Lab\n"
    "Click **Collect reels** or send `collect #finance` / `collect personal finance` to target 25 readable reel records (up to 50). Send `extract #finance` / `extract personal finance` for the separate legacy five-candidate flow. "
    "Results may be stale or incomplete and are not a trend or popularity report. Use only public content you are authorized to access; the app does not verify authorization or guarantee a logged-out browser.\n\n"
    "You can also analyze a CSV or try the secondary synthetic demo. Nothing is published."
)
TRANSCRIBE_MAX_BYTES = 100 * 1024 * 1024
TRANSCRIBE_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _md(value: object) -> str:
    """Escape user supplied text before placing it in rendered Markdown."""
    import html

    safe = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>:])", r"\\\1", safe)


def _format_rate(value: object) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return str(value)


def _format_lift(value: object) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return str(value)


def _render_temporal_comparison(comparison: dict | None) -> str:
    """Render source-backed historical cohorts without implying live trends."""
    if not isinstance(comparison, dict):
        return ""
    coverage = comparison.get("date_coverage") or {}
    minimums = comparison.get("minimums") or {}
    window_days = comparison.get("window_days", "unknown")
    anchor = comparison.get("anchor_date") or "unknown"
    lines = [
        "## Retrospective publication-cohort comparison",
        "This compares counts in adjacent historical windows from the supplied dataset. It is not a live trend feed and does not measure metric velocity.",
        "Candidate topics use the dataset's content-pillar labels as a proxy.",
        f"- **Dataset coverage:** {_md(coverage.get('start') or 'unknown')} to {_md(coverage.get('end') or 'unknown')} ({_md(coverage.get('record_count', 0))} records; {_md(coverage.get('distinct_creator_count', 0))} creators).",
        f"- **Window definition:** {_md(window_days)} calendar days per window, inclusive; newest window ends {_md(anchor)} and baseline is the immediately preceding window.",
        f"- **Minimum sample:** {_md(minimums.get('posts_per_window', 'unknown'))} posts and {_md(minimums.get('distinct_creators_per_window', 'unknown'))} distinct creators in each window.",
    ]
    comparisons = comparison.get("comparisons") or []
    if not comparisons:
        lines.extend(["", "No dated candidate topics were available to compare."])
        return "\n".join(lines)
    if len(comparisons) > 20:
        lines.extend(["", f"Showing 20 of {_md(len(comparisons))} candidate topics to keep the report readable."])

    for item in comparisons[:20]:
        if not isinstance(item, dict):
            continue
        recent = item.get("recent_window") or {}
        baseline = item.get("baseline_window") or {}
        lines.extend([
            "",
            f"### {_md(item.get('candidate_kind') or 'content pillar')}: {_md(item.get('candidate') or 'Unlabeled candidate')}",
            f"- **Recent window:** {_md(recent.get('start') or 'unknown')} to {_md(recent.get('end') or 'unknown')} — {_md(recent.get('post_count', 0))} posts from {_md(recent.get('distinct_creator_count', 0))} creators.",
            f"- **Baseline window:** {_md(baseline.get('start') or 'unknown')} to {_md(baseline.get('end') or 'unknown')} — {_md(baseline.get('post_count', 0))} posts from {_md(baseline.get('distinct_creator_count', 0))} creators.",
        ])
        status = item.get("status")
        if status == "comparable":
            change = item.get("post_count_change")
            try:
                change_text = f"{int(change):+d} posts"
            except (TypeError, ValueError):
                change_text = "count change unavailable"
            lines.append(f"- **Comparison:** comparable sample; raw post-count change (recent minus baseline): {_md(change_text)}. This is a count difference, not a rate or velocity.")
        elif status == "zero_baseline":
            lines.append("- **Comparison:** not enough comparable baseline data; the baseline has zero posts, so no change is calculated.")
        elif status == "insufficient_recent_data":
            lines.append("- **Comparison:** not enough comparable recent data to meet the minimum sample and creator thresholds.")
        elif status == "insufficient_baseline_data":
            lines.append("- **Comparison:** not enough comparable baseline data to meet the minimum sample and creator thresholds.")
        else:
            lines.append(f"- **Comparison:** not enough comparable data (status: {_md(status or 'unknown')}).")
        for window_name, label in (("baseline_window", "Baseline sources"), ("recent_window", "Recent sources")):
            window = item.get(window_name) or {}
            sources = window.get("source_records") or []
            lines.append(f"- **{label}:**")
            if not sources:
                lines.append("  - No source records in this window.")
                continue
            for source in sources[:10]:
                if not isinstance(source, dict):
                    continue
                detail = f"{source.get('posted_at') or 'unknown date'} · {source.get('creator') or 'unknown creator'}"
                if source.get("post_url"):
                    detail += f" · {source['post_url']}"
                lines.append(f"  - {_md(detail)}")
            if len(sources) > 10:
                lines.append(f"  - Showing 10 of {_md(len(sources))} source records.")
    return "\n".join(lines)


def _render_analysis(result: dict) -> str:
    lines = [
        "## Dataset overview",
        f"- **Niche:** {_md(result.get('niche') or 'not specified')}",
        f"- **Records:** {_md(result.get('record_count', 0))}",
    ]
    outliers = result.get("ranked_outliers") or []
    if outliers:
        lead = outliers[0]
        lines.extend(
            [
                "",
                "## First signal to inspect",
                (
                    f"**{_md(lead.get('creator') or 'Unknown creator')} · "
                    f"{_md(lead.get('pillar') or 'Unlabeled pillar')}** ranked first in this dataset "
                    f"at {_format_rate(lead.get('engagement_rate'))} engagement versus a "
                    f"{_format_rate(lead.get('peer_median'))} peer median "
                    f"({_md(_format_lift(lead.get('lift')))} lift; {_md(lead.get('benchmark_count', 'unknown'))} peers)."
                ),
                "This is a retrospective comparison within the supplied records, not a prediction or live trend.",
                "Try `draft 1` to create a local draft from the topic and your profile; nothing is published.",
            ]
        )
    notes = result.get("data_notes") or []
    if notes:
        lines.extend(["", "### Data notes", *[f"- {_md(note)}" for note in notes]])

    lines.extend(["", "## Ranked posts", "Posts are ranked from the supplied dataset; this is not a live trend feed."])
    if not outliers:
        lines.extend(["", "No comparable outlier posts were found. More records or metrics may be needed."])
    for index, post in enumerate(outliers[:10], start=1):
        creator = post.get("creator") or "unknown creator"
        pillar = post.get("pillar") or "unlabeled pillar"
        lines.extend(
            [
                "",
                f"### {index}. {_md(creator)} · {_md(pillar)}",
                f"- **Date:** {_md(post.get('posted_at') or 'unknown')}",
                f"- **Engagement rate:** {_format_rate(post.get('engagement_rate'))}",
                f"- **Peer median:** {_format_rate(post.get('peer_median'))} · **Lift:** {_md(_format_lift(post.get('lift')))} · **Peers:** {_md(post.get('benchmark_count', 'unknown'))}",
                f"- **Why ranked:** {_md(post.get('reason') or 'No explanation supplied.')}",
                f"- **Post:** {_md((post.get('content') or '').strip() or '(no text)')}",
            ]
        )
        if post.get("post_url"):
            lines.append(f"- **Source:** {_md(post['post_url'])}")

    lines.extend(["", "## Creators"])
    creators = result.get("creator_stats") or []
    if creators:
        lines.extend(
            f"- **{_md(item.get('creator', 'Unknown'))}** — {_md(item.get('post_count', 0))} posts; "
            f"{_md(item.get('total_views') if item.get('total_views') is not None else 'unknown')} total views; "
            f"{_format_rate(item.get('avg_engagement_rate'))} average engagement"
            for item in creators[:10]
        )
    else:
        lines.append("- No creator summary available.")

    lines.extend(["", "## Content pillars"])
    pillars = result.get("content_pillars") or []
    if pillars:
        lines.extend(
            f"- **{_md(item.get('pillar') or 'Unlabeled')}** — {_md(item.get('post_count') or 0)} posts; "
            f"{_format_rate(item.get('avg_engagement_rate'))} average engagement"
            for item in pillars[:10]
        )
    else:
        lines.append("- No labeled content pillars available.")

    temporal = _render_temporal_comparison(result.get("temporal_comparison"))
    if temporal:
        lines.extend(["", temporal])

    lines.extend(
        [
            "",
            "To draft from a ranked post, use `draft <number>` (for example, `draft 1`).",
            "Set or update your creator profile with `profile`.",
        ]
    )
    return "\n".join(lines)


def _instagram_records(observations: list[dict[str, str]]) -> list[dict]:
    """Adapt visible page text to the keyword analyzer without inventing metadata."""
    records = []
    for observation in observations:
        text = observation.get("text", "").strip()
        source_url = observation.get("source_url")
        if not text or not source_url:
            continue
        creator = observation.get("creator")
        if (not isinstance(creator, str) or not _INSTAGRAM_CREATOR_RE.fullmatch(creator)
                or creator.lower() in _INSTAGRAM_RESERVED_PATHS):
            creator = None
        records.append({
            "creator": creator,
            "content": text,
            "platform": "Instagram",
            "post_url": source_url,
            "posted_at": observation.get("posted_at")
            if isinstance(observation.get("posted_at"), str) else None,
        })
    return records


def _manual_instagram_records(value: str) -> list[dict]:
    """Parse user-pasted public excerpts as URL | text, with no browser access."""
    records = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        if "|" not in line:
            raise ValueError(f"line {line_number} must use URL | visible public text")
        url, content = (part.strip() for part in line.split("|", maxsplit=1))
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"line {line_number} has a malformed URL") from exc
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or not host
                or (host != "instagram.com" and not host.endswith(".instagram.com"))
                or parsed.username is not None or parsed.password is not None
                or port not in (None, 443)):
            raise ValueError(f"line {line_number} must use an HTTPS Instagram URL")
        if not content:
            raise ValueError(f"line {line_number} has no pasted text")
        records.append({
            "creator": "creator not identified", "content": content[:4_000],
            "platform": "Instagram", "post_url": url, "posted_at": None,
        })
    if len(records) > 5:
        raise ValueError("paste at most five URL and text lines at a time")
    return records


def _analyze_instagram_text(records: list[dict], keyword: str) -> dict:
    """Match only supplied post text, then attach URLs as source provenance."""
    matches = []
    idea = punchline = draft_suggestion = None
    for record in records:
        text_only = {key: value for key, value in record.items() if key != "post_url"}
        result = analyze_keyword_insights([text_only], [keyword])
        item = result["keywords"][0]
        if item["matches"]:
            match = item["matches"][0]
            match["post_url"] = record.get("post_url")
            matches.append(match)
            if idea is None:
                idea = item.get("idea")
                punchline = item.get("punchline")
                draft_suggestion = item.get("draft_suggestion")
    return {
        "record_count": len(records),
        "keywords": [{
            "keyword": keyword,
            "match_count": len(matches),
            "matches": matches,
            "idea": idea,
            "punchline": punchline,
            "draft_suggestion": draft_suggestion,
        }],
    }


def _render_instagram_search(result: dict) -> str:
    """Render a user-run search link without implying the app fetched results."""
    search_url = quote(result["url"], safe=":/?#[]@!$&'()*+,;=%-._~")
    return "\n".join([
        "## Search link for public Instagram posts and reels",
        f"- **Search phrase:** {_md(result['query'])}",
        f"- [Open Google search results](<{search_url}>)",
        "The app has not run this search, read results, or verified any post. Google’s index may be incomplete or stale.",
        "Review results yourself and choose a public post or reel you are authorized to view. Open it in the headed browser used by this app, then send `instagram <keyword>` or `instagram urls` to analyze visible post text if available.",
        "This link is a discovery aid, not an Instagram feed, trend report, or popularity ranking.",
    ])


def _render_instagram_extraction(result: dict, keywords: str, *, target_readable: int | None = None) -> str:
    """Render separately sourced search snippets and visible post text."""
    effective_phrase = normalize_instagram_phrase(keywords)
    raw_normalized_terms = " ".join(keywords.split()).casefold()
    capture_terms = " ".join(keywords.split())
    phrase_was_normalized = effective_phrase.casefold() != raw_normalized_terms
    target_mode = target_readable is not None
    lines = [
        "## Instagram reel collection" if target_mode else "## Instagram post candidates",
        (f"This bounded collection targeted {target_readable} readable records (maximum 50) and may return fewer. The index may be stale or incomplete; this is not a live Instagram search, popularity ranking, or trend report."
         if target_mode else "This is a best-effort sample of at most five candidates from visible Google results. The index may be stale or incomplete; this is not a live Instagram search, popularity ranking, or trend report."),
        "Google snippets are shown only as search-result context. Keyword matches and draft suggestions below use visible text collected from the Instagram post page, never the search snippet.",
        "Use only public posts you are authorized to access. The app does not verify authorization or guarantee that the headed browser is logged out.",
    ]
    candidates = result.get("candidates") or []
    sampled_candidates = candidates[:100] if target_mode else candidates[:5]
    readable_candidates = [
        candidate for candidate in sampled_candidates
        if isinstance(candidate, dict) and candidate.get("status") == "read"
        and isinstance(candidate.get("post_text"), str) and candidate["post_text"].strip()
        and (not target_mode or candidate.get("source_association") == "verified")
    ]
    readable_count = len(readable_candidates)
    match_count = 0
    dated_count = 0
    for candidate in readable_candidates:
        observation = {"source_url": candidate.get("source_url"), "text": candidate["post_text"]}
        observed_date = candidate.get("posted_at")
        if isinstance(observed_date, str):
            observation["posted_at"] = observed_date
            try:
                datetime.strptime(observed_date, "%Y-%m-%d")
            except ValueError:
                pass
            else:
                dated_count += 1
        if isinstance(candidate.get("creator"), str):
            observation["creator"] = candidate["creator"]
        records = _instagram_records([observation])
        if not records:
            continue
        try:
            insight = _analyze_instagram_text(records, effective_phrase)["keywords"][0]
        except ValueError:
            insight = None
        if insight and insight["match_count"]:
            match_count += 1
    candidate_count = len(sampled_candidates)
    lines.extend([
        "",
        "### Evidence summary · bounded Google-indexed sample",
        f"**Search query:** {_md(result.get('query') or keywords)} · **Search run:** {_md(result.get('searched_at') or 'time unavailable')}",
        f"- **Visible-text matches for {_md(effective_phrase)}:** {match_count} of {readable_count} readable posts. Matches exclude URL fields and Google snippets.",
        f"- **Readable post pages:** {readable_count} of {candidate_count} candidates.",
        f"- **Observed dates:** {dated_count} of {readable_count} readable posts.",
    ])
    if target_mode:
        attempted = result.get("attempted_count", candidate_count)
        # Recompute from candidate evidence so renderer totals cannot include
        # any caption whose source association is missing or unverified.
        reported_readable = readable_count
        stop_reason = result.get("stop_reason") or "not reported"
        lines.extend([
            f"- **Target:** {target_readable} readable reels; **collected:** {_md(reported_readable)}.",
            f"- **Candidate attempts:** {_md(attempted)}; **stop reason:** {_md(stop_reason)}.",
        ])
        if isinstance(reported_readable, int) and reported_readable < target_readable:
            lines.append(f"- **Partial result:** returned {_md(reported_readable)} of {target_readable} requested readable reels.")
    if phrase_was_normalized:
        lines.extend([
            f"- **Input as entered:** {_md(keywords)}",
            f"- **Effective phrase used for matching:** {_md(effective_phrase)}",
            f"- **Capture command for this result:** {_md(f'capture {capture_terms}')}",
        ])
    if not candidates:
        lines.extend(["", "No public post or reel candidates were available in the visible search results."])
    for index, candidate in enumerate(sampled_candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        url = candidate.get("source_url")
        safe_url = quote(url, safe=":/?#[]@!$&'()*+,;=%-._~") if isinstance(url, str) else ""
        lines.extend(["", f"### Candidate {index}"])
        if safe_url:
            lines.append(f"- **Source:** [Instagram post/reel](<{safe_url}>)")
        status = candidate.get("status") or "unknown"
        lines.append(f"- **Collection status:** {_md(status)}")
        if candidate.get("search_result_text"):
            lines.append(f"- **Google search snippet (unverified):** {_md(candidate['search_result_text'])}")
        post_text = candidate.get("post_text")
        association_verified = candidate.get("source_association") == "verified"
        has_visible_text = isinstance(post_text, str) and bool(post_text.strip())
        if target_mode and has_visible_text and not association_verified:
            lines.append("- **Source association:** unverified; visible caption text was found, but it cannot be attributed to this reel and is excluded from counts and analysis.")
        elif status == "read" and has_visible_text and (not target_mode or association_verified):
            lines.append("- **Collected visible post/caption text (first 1,000 characters):**")
            lines.append(f"  - {_md(post_text[:1_000])}")
            if len(post_text) > 1_000:
                lines.append("  - Text clipped in this report; the collector returned a bounded excerpt.")
            observation = {"source_url": url, "text": post_text}
            if isinstance(candidate.get("posted_at"), str):
                observation["posted_at"] = candidate["posted_at"]
            if isinstance(candidate.get("creator"), str):
                observation["creator"] = candidate["creator"]
            record = _instagram_records([observation])[0]
            if record.get("creator"):
                lines.append(f"- **Observed creator:** {_md(record['creator'])} (from the visible post scope).")
            else:
                lines.append("- **Creator:** creator not identified.")
            try:
                insight = _analyze_instagram_text([record], effective_phrase)["keywords"][0]
            except ValueError:
                insight = None
            if insight and insight["matches"]:
                match = insight["matches"][0]
                lines.append(f"- **Visible post text match:** {_md(effective_phrase)}")
                lines.append(f"  - **Date:** {_md(match.get('posted_at') or 'not provided')}")
                lines.append(f"  - **Excerpt:** {_md(match.get('excerpt') or '')}")
                if insight.get("punchline"):
                    lines.append(f"  - **Punchline:** {_md(insight['punchline'])}")
                if insight.get("draft_suggestion"):
                    lines.append(f"  - **Editable draft suggestion:** {_md(insight['draft_suggestion'])}")
                    lines.append("  - Keep the source attribution above; add only an example and result you can verify.")
            else:
                lines.append(f"- No match for {_md(effective_phrase)} in the visible post text.")
        elif status == "unavailable":
            if candidate.get("error"):
                lines.append(f"- **Why unavailable:** {_md(candidate['error'])}")
            lines.append("- Search-result text was not treated as a collected post.")
        else:
            lines.append("- No visible post text was collected; search-result text was not treated as a post.")
    # In target mode, only verified source associations are eligible signals.
    # Preserve every row so the helper's candidate denominator remains correct.
    signal_candidates = candidates
    if target_mode:
        signal_candidates = [
            ({**candidate, "status": "unverified"}
             if isinstance(candidate, dict)
             and candidate.get("source_association") != "verified"
             else candidate)
            for candidate in candidates
        ]
    signal_result = analyze_topic_signals(signal_candidates, effective_phrase)
    lines.extend([
        "",
        "## Co-occurring hashtags in readable posts",
        f"Counted distinct readable posts: {signal_result['read_post_count']} of {signal_result['candidate_count']} candidates.",
        "Counts use hashtags in collected post/caption text only; Google snippets are excluded. The explicitly searched hashtag is excluded when the query contains one.",
    ])
    if signal_result["signals"]:
        lines.extend(
            f"- **{_md(signal['hashtag'])}** — {signal['post_count']} of {signal_result['read_post_count']} readable posts."
            for signal in signal_result["signals"]
        )
        if signal_result["truncated"]:
            lines.append("- Top 10 shown; additional co-occurring hashtags were omitted.")
    elif signal_result["read_post_count"] == 0:
        if target_mode and any(
            isinstance(candidate, dict) and candidate.get("post_text")
            and candidate.get("source_association") != "verified"
            for candidate in candidates
        ):
            lines.append("- No signals computed because the available caption text had unverified source associations.")
        else:
            lines.append("- No signals computed because no post text was readable.")
    else:
        lines.append("- No co-occurring hashtags were present in the readable post text after excluding the searched hashtag, if specified.")
    lines.append(_md(signal_result["data_note"]))
    errors = result.get("errors") or []
    if errors:
        lines.extend(["", "### Search or cleanup notes", *[f"- {_md(error)}" for error in errors]])
    if result.get("provenance_note"):
        lines.extend(["", _md(result["provenance_note"])])
    return "\n".join(lines)


def _snapshot_candidate_metadata(candidates: list[dict]) -> list[dict]:
    """Select only supported URL/status/date fields; never retain text/snippets."""
    metadata = []
    for candidate in candidates[:5]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("source_url"), str):
            continue
        has_text = candidate.get("status") == "read" and isinstance(candidate.get("post_text"), str) and bool(candidate["post_text"].strip())
        item = {
            "url": candidate["source_url"],
            "status": "read" if has_text else "unreadable",
        }
        if isinstance(candidate.get("posted_at"), str):
            item["observed_date"] = candidate["posted_at"]
        metadata.append(item)
    return metadata


def _snapshot_topic_signal_metadata(candidates: list[dict], keywords: str) -> dict | None:
    """Keep only aggregate hashtag counts, never text or post-level associations."""
    analysis = analyze_topic_signals(candidates, keywords, limit=20)
    denominator = analysis["read_post_count"]
    if denominator == 0:
        return None
    return {
        "read_denominator": denominator,
        "tags": [
            {"tag": item["hashtag"], "count": item["post_count"]}
            for item in analysis["signals"]
        ],
        "truncated": analysis.get("truncated", False),
    }


def _latest_search_matches_terms(latest: object, terms: str) -> bool:
    """Require the explicit capture term to match the current search sample."""
    normalized = " ".join(terms.split()).casefold()
    return isinstance(latest, dict) and latest.get("terms") == normalized


def _render_snapshot_comparison(result: dict, keywords: str) -> str:
    """Render only recurring/new/absent URLs from two captured samples."""
    lines = [
        "## Repeated Google-indexed sample comparison",
        f"**Terms:** {_md(keywords)}",
    ]
    if result.get("status") != "compared_local_observations":
        lines.extend([
            "Not enough comparable saved observations.",
            f"- **Reason:** {_md(result.get('reason') or 'comparison unavailable')}",
            "Capture two searches for the same terms and query, each with at least one readable post.",
            "This comparison does not report zero activity or Instagram-wide trends.",
        ])
        return "\n".join(lines)

    previous = result.get("previous") or {}
    latest = result.get("latest") or {}
    lines.extend([
        "This compares only URLs in two explicitly saved, bounded Google-indexed samples. It does not measure posting growth, freshness, popularity, or Instagram-wide trends.",
        f"- **Earlier sample:** {_md(previous.get('captured_at'))} — {_md(previous.get('successfully_read_count', 0))} readable of {_md(previous.get('candidate_count', 0))} candidates.",
        f"- **Later sample:** {_md(latest.get('captured_at'))} — {_md(latest.get('successfully_read_count', 0))} readable of {_md(latest.get('candidate_count', 0))} candidates.",
    ])
    topic_comparison = result.get("topic_signals") or {}
    lines.extend(["", "### Co-occurring hashtag counts in readable text"])
    if topic_comparison.get("status") != "available":
        lines.append("- Aggregate counts are unavailable for one or both snapshots (for example, an older snapshot may not contain them). Missing aggregates are unavailable, not zero.")
    else:
        old_topics = topic_comparison.get("previous") or {}
        new_topics = topic_comparison.get("latest") or {}
        old_denominator = old_topics.get("read_denominator", 0)
        new_denominator = new_topics.get("read_denominator", 0)
        old_truncated = bool(old_topics.get("truncated"))
        new_truncated = bool(new_topics.get("truncated"))
        old_counts = {item["tag"].casefold(): item["count"] for item in old_topics.get("tags", [])}
        new_counts = {item["tag"].casefold(): item["count"] for item in new_topics.get("tags", [])}
        display = {item["tag"].casefold(): item["tag"] for item in old_topics.get("tags", [])}
        display.update({item["tag"].casefold(): item["tag"] for item in new_topics.get("tags", [])})
        if not display:
            if old_truncated or new_truncated:
                lines.append("- No tags are listed in the saved aggregate; at least one aggregate was truncated, so omitted tags are unknown.")
            else:
                lines.append(f"- No co-occurring hashtags were observed in either sample ({old_denominator} and {new_denominator} readable posts).")
        else:
            lines.append("- Counts are posts containing the hashtag, shown as count/readable-posts; tags omitted from a truncated sample are unknown, not zero.")
            for key in sorted(display):
                old_value = f"{old_counts[key]}/{old_denominator}" if key in old_counts else ("unknown" if old_truncated else f"0/{old_denominator}")
                new_value = f"{new_counts[key]}/{new_denominator}" if key in new_counts else ("unknown" if new_truncated else f"0/{new_denominator}")
                lines.append(f"- **{_md(display[key])}** — earlier {old_value}; later {new_value}.")
        if old_truncated or new_truncated:
            lines.append("- At least one saved aggregate was capped at 20 tags; additional tags are omitted.")
    lines.append("These counts compare aggregate co-occurrence in two collected samples only; they do not indicate rising or falling trends, popularity, or platform-wide frequency.")
    for key, label in (
        ("recurring_urls", "URLs observed in both samples"),
        ("newly_observed_urls", "URLs observed only in the later sample"),
        ("no_longer_observed_urls", "URLs not in the later sample"),
    ):
        urls = result.get(key) or []
        lines.extend(["", f"### {label}"])
        if not urls:
            lines.append("- None in these samples.")
        for url in urls[:5]:
            safe_url = quote(url, safe=":/?#[]@!$&'()*+,;=%-._~")
            lines.append(f"- [Instagram post/reel](<{safe_url}>)")
    lines.append("A URL first observed later may still point to an older post; search-index changes are not a post timeline.")
    return "\n".join(lines)


async def _send_instagram_search(keywords: str) -> None:
    try:
        search = build_instagram_search(keywords)
    except ValueError as exc:
        await cl.Message(content=f"Could not build a search link: {_md(exc)}").send()
        return
    await cl.Message(content=_render_instagram_search(search)).send()


async def _send_instagram_extraction(keywords: str) -> None:
    if not _PUBLIC_SEARCH_ADMISSION.acquire(blocking=False):
        await cl.Message(content=(
            "A public post search is already running. Wait for its result or failure before starting another; "
            "the new request was not queued."
        )).send()
        return
    try:
        generation = _begin_public_search()
        cl.user_session.set("socialrum_latest_public_search", None)
        await cl.Message(content=(
            f"Searching visible Google results for **{_md(keywords)}** and checking up to five public post/reel pages. "
            "This may take a few minutes; I’ll report what was readable when the search finishes."
        )).send()
        worker_future = _PUBLIC_SEARCH_EXECUTOR.submit(
            search_and_collect_public_posts, keywords
        )
    except BaseException:
        _PUBLIC_SEARCH_ADMISSION.release()
        raise
    # Release only when the underlying blocking call actually ends. Cancelling
    # the chat task cannot stop CDP work already running in the worker thread.
    worker_future.add_done_callback(lambda _future: _PUBLIC_SEARCH_ADMISSION.release())
    try:
        result = await asyncio.wrap_future(worker_future)
    except (ValueError, RuntimeError) as exc:
        await cl.Message(content=f"Could not complete public post discovery: {_md(exc)}").send()
        return
    except Exception:
        logger.exception("Unexpected Instagram search/collection failure")
        await cl.Message(content="Could not complete public post discovery. Check the local app logs; no results are assumed.").send()
        return
    if not _is_current_public_search(generation):
        await cl.Message(content="An earlier extraction finished after a newer request and was discarded; it cannot be captured.").send()
        return
    await cl.Message(content=_render_instagram_extraction(result, keywords)).send()
    if not _is_current_public_search(generation):
        await cl.Message(content="This extraction was superseded by a newer request and cannot be captured.").send()
        return
    cl.user_session.set("socialrum_latest_public_search", {
        "terms": " ".join(keywords.split()).casefold(),
        "generation": generation,
        "query": result.get("query"),
        "search_engine": result.get("search_engine"),
        "searched_at": result.get("searched_at"),
        "candidates": _snapshot_candidate_metadata(result.get("candidates") or []),
        "topic_signals": _snapshot_topic_signal_metadata(
            result.get("candidates") or [], normalize_instagram_phrase(keywords)
        ),
    })


async def _send_instagram_target_collection(keywords: str, target_readable: int = 25) -> None:
    """Run the separate readable-reel workflow; leave legacy `extract` bounded at five."""
    if not isinstance(target_readable, int) or isinstance(target_readable, bool) or not 1 <= target_readable <= 50:
        await cl.Message(content="Readable reel target must be a whole number from 1 to 50.").send()
        return
    if not keywords.strip():
        await cl.Message(content="Enter a hashtag or keyword phrase before collecting reels.").send()
        return
    if not _PUBLIC_SEARCH_ADMISSION.acquire(blocking=False):
        await cl.Message(content="A public post search is already running. Wait for its result or failure before starting another; the new request was not queued.").send()
        return
    loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    progress_active = threading.Event()
    progress_active.set()
    progress_message = cl.Message(content=(
        f"Collecting reels for **{_md(keywords)}** with a target of **{target_readable} readable records**. "
        "This may take several minutes. I’ll report candidate attempts, readable records, and any shortfall when the bounded search finishes."
    ))

    def enqueue_progress(snapshot: dict) -> None:
        if not progress_active.is_set():
            return
        if progress_queue.full():
            try:
                progress_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        progress_queue.put_nowait(snapshot)

    def report_progress(snapshot: dict) -> None:
        if not progress_active.is_set() or not isinstance(snapshot, dict):
            return
        try:
            loop.call_soon_threadsafe(enqueue_progress, dict(snapshot))
        except RuntimeError:
            # The chat loop may already be shutting down after cancellation.
            progress_active.clear()

    async def update_progress_message() -> None:
        last_update = 0.0
        try:
            while progress_active.is_set():
                snapshot = await progress_queue.get()
                while not progress_queue.empty():
                    snapshot = progress_queue.get_nowait()
                delay = max(0.0, last_update + 1.0 - loop.time())
                if delay:
                    await asyncio.sleep(delay)
                if not progress_active.is_set():
                    return
                attempted = snapshot.get("attempted_count", 0)
                readable = snapshot.get("readable_count", 0)
                pages = snapshot.get("pages_visited", 0)
                progress_message.content = (
                    f"Collecting reels for **{_md(keywords)}** · "
                    f"candidate attempts: **{_md(attempted)}** · "
                    f"verified readable: **{_md(readable)} / {target_readable}** · "
                    f"search pages visited: **{_md(pages)}**."
                )
                await progress_message.update()
                last_update = loop.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Could not update reel collection progress message", exc_info=True)

    progress_task = asyncio.create_task(update_progress_message())
    try:
        generation = _begin_public_search()
        cl.user_session.set("socialrum_latest_public_search", None)
        await progress_message.send()
        worker_future = _PUBLIC_SEARCH_EXECUTOR.submit(
            search_and_collect_public_posts, keywords,
            target_readable=target_readable, reels_only=True,
            progress_callback=report_progress,
        )
    except BaseException:
        progress_active.clear()
        progress_task.cancel()
        await asyncio.gather(progress_task, return_exceptions=True)
        _PUBLIC_SEARCH_ADMISSION.release()
        raise
    worker_future.add_done_callback(lambda _future: _PUBLIC_SEARCH_ADMISSION.release())
    try:
        result = await asyncio.wrap_future(worker_future)
    except (ValueError, RuntimeError) as exc:
        await cl.Message(content=f"Could not complete reel collection: {_md(exc)}").send()
        return
    except Exception:
        logger.exception("Unexpected Instagram target collection failure")
        await cl.Message(content="Could not complete reel collection. Check the local app logs; no results are assumed.").send()
        return
    finally:
        # Stop and join the UI bridge before rendering final results. A callback
        # already queued by the worker is ignored once this flag is cleared.
        progress_active.clear()
        progress_task.cancel()
        await asyncio.gather(progress_task, return_exceptions=True)
    if not _is_current_public_search(generation):
        await cl.Message(content="An earlier collection finished after a newer request and was discarded.").send()
        return
    progress_message.content = (
        f"Collection finished for **{_md(keywords)}** · candidate attempts: "
        f"**{_md(result.get('attempted_count', 0))}** · verified readable: "
        f"**{_md(result.get('readable_count', 0))} / {target_readable}** · "
        f"stop reason: **{_md(result.get('stop_reason') or 'not reported')}**."
    )
    await progress_message.update()
    await cl.Message(content=_render_instagram_extraction(result, keywords, target_readable=target_readable)).send()


async def _prompt_instagram_target_collection() -> None:
    response = await cl.AskUserMessage(content=REEL_COLLECTION_PROMPT, timeout=180).send()
    keywords = (response.get("output") or "").strip() if response else ""
    if not keywords:
        await cl.Message(content="No hashtag or phrase supplied. Nothing was searched.").send()
        return
    target_response = await cl.AskUserMessage(
        content="How many readable reels should I target? Enter 1–50; leave blank for 25.", timeout=180,
    ).send()
    raw_target = (target_response.get("output") or "").strip() if target_response else ""
    try:
        target = 25 if not raw_target else int(raw_target)
    except ValueError:
        await cl.Message(content="Enter a whole number from 1 to 50; nothing was searched.").send()
        return
    await _send_instagram_target_collection(keywords, target)


def _begin_public_search() -> int:
    """Advance a per-chat generation so older concurrent searches cannot commit."""
    previous = cl.user_session.get(PUBLIC_SEARCH_GENERATION_KEY)
    generation = previous + 1 if isinstance(previous, int) and not isinstance(previous, bool) else 1
    cl.user_session.set(PUBLIC_SEARCH_GENERATION_KEY, generation)
    return generation


def _is_current_public_search(generation: int) -> bool:
    return cl.user_session.get(PUBLIC_SEARCH_GENERATION_KEY) == generation


async def _prompt_instagram_extraction() -> None:
    response = await cl.AskUserMessage(
        content=INSTAGRAM_KEYWORD_PROMPT,
        timeout=180,
    ).send()
    keywords = (response.get("output") or "").strip() if response else ""
    if not keywords:
        await cl.Message(content="No hashtag or phrase supplied. Nothing was searched.").send()
        return
    await _send_instagram_extraction(keywords)


async def _capture_latest_search(keywords: str) -> None:
    latest = cl.user_session.get("socialrum_latest_public_search")
    if not _latest_search_matches_terms(latest, keywords):
        await cl.Message(content="No matching search is ready to capture. Run `extract <same hashtag or phrase>` first; capture does not repeat the search.").send()
        return
    generation = latest.get("generation")
    if not isinstance(generation, int) or not _is_current_public_search(generation):
        await cl.Message(content="That extraction has been superseded. Run a new `extract` before capturing.").send()
        return
    # Reserve/consume the result synchronously before awaiting storage so two
    # concurrent capture commands cannot persist the same search twice.
    cl.user_session.set("socialrum_latest_public_search", None)
    try:
        snapshot = await asyncio.to_thread(
            save_snapshot,
            keywords,
            latest["query"],
            SNAPSHOT_SOURCE,
            SNAPSHOT_SOURCE_VERSION,
            latest["candidates"],
            SNAPSHOT_STORE,
            observed_at=latest["searched_at"],
            topic_signals=latest.get("topic_signals"),
        )
    except (KeyError, OSError, ValueError) as exc:
        if (_is_current_public_search(generation)
                and cl.user_session.get("socialrum_latest_public_search") is None):
            cl.user_session.set("socialrum_latest_public_search", latest)
        await cl.Message(content=f"Could not save this snapshot: {_md(exc)}").send()
        return
    await cl.Message(content=(
        f"Captured search metadata locally as `{_md(snapshot['id'])}` at {_md(snapshot['captured_at'])}. "
        "Saved terms, query, candidate URLs, observed dates, and read status; when readable posts were available, an aggregate of co-occurring hashtag counts was saved too. Captions, search snippets, and per-post hashtag associations were not saved. "
        "Run `compare <same hashtag or phrase>` after capturing another matching search."
    )).send()


async def _compare_saved_searches(keywords: str) -> None:
    try:
        search = build_instagram_search(keywords)
        comparison = await asyncio.to_thread(
            compare_latest_snapshots,
            keywords,
            search["query"],
            SNAPSHOT_SOURCE,
            SNAPSHOT_SOURCE_VERSION,
            SNAPSHOT_STORE,
        )
    except (OSError, ValueError) as exc:
        await cl.Message(content=f"Could not compare saved searches: {_md(exc)}").send()
        return
    await cl.Message(content=_render_snapshot_comparison(comparison, keywords)).send()


async def _forget_saved_searches(keywords: str) -> None:
    try:
        removed = await asyncio.to_thread(delete_snapshots, keywords, SNAPSHOT_STORE)
    except (OSError, ValueError) as exc:
        await cl.Message(content=f"Could not delete saved searches: {_md(exc)}").send()
        return
    await cl.Message(content=f"Deleted {removed} locally saved snapshot(s) for `{_md(keywords)}`.").send()


def _render_keyword_insights(
    result: dict, analyzed_at: datetime, *, source_description: str
) -> str:
    """Render local keyword matches while accurately describing their source."""
    timestamp = analyzed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "## Instagram keyword snapshot",
        f"Snapshot report generated at: {timestamp}.",
        source_description,
        "Keyword matches are based on supplied visible text only. Source URLs are shown as provenance and do not count as matches.",
        "Use only public content you are authorized to access; the app does not verify authorization or guarantee logged-out browser state.",
        "This is not a platform-wide search, trend, or popularity ranking. Use only public pages you are authorized to view and respect Instagram's terms.",
        f"- **Visible observations:** {_md(result.get('record_count', 0))}",
    ]
    for item in result.get("keywords") or []:
        if not isinstance(item, dict):
            continue
        keyword = item.get("keyword") or "(empty keyword)"
        count = item.get("match_count", 0)
        lines.extend(["", f"### Keyword: {_md(keyword)}", f"- **Matches:** {_md(count)} matching observation(s)"])
        matches = item.get("matches") or []
        for match in matches[:10]:
            if not isinstance(match, dict):
                continue
            lines.append(
                f"- **Source:** {_md(match.get('creator') or 'creator not identified')} · "
                f"Date: {_md(match.get('posted_at') or 'not provided')} · "
                f"Matched in: {_md(', '.join(match.get('matched_in') or []))}"
            )
            if match.get("excerpt"):
                lines.append(f"  - {_md(match['excerpt'])}")
            if match.get("post_url"):
                # Angle form plus quoting keeps user-supplied paths inside the link.
                source_url = quote(match["post_url"], safe=":/?#[]@!$&'()*+,;=%-._~")
                lines.append(f"  - [Instagram source](<{source_url}>)")
        if len(matches) > 10:
            lines.append(f"- Showing 10 of {_md(len(matches))} matching observations.")
        if item.get("idea"):
            lines.extend([f"- **Possible angle:** {_md(item['idea'])}", f"- **Punchline:** {_md(item.get('punchline') or '')}"])
        else:
            lines.append("- No supplied visible text matched this keyword.")
        if item.get("draft_suggestion"):
            lines.extend([
                "- **Editable draft suggestion (grounded in the supplied excerpt):**",
                f"  - {_md(item['draft_suggestion'])}",
                "  - Keep the source attribution above; add only an example and result you can verify.",
            ])
    if result.get("data_note"):
        lines.extend(["", _md(result["data_note"])])
    return "\n".join(lines)


async def _instagram_keyword_flow(keyword: str | None = None) -> None:
    """Ask for Instagram URLs, collect only on explicit command, then analyze locally."""
    disclosure = (
        "Provide public Instagram page URLs, one per line. Each URL's Instagram host and path must match a page already open in a headed browser tab; query strings and fragments are ignored for tab matching. "
        "The app reads visible post text from that matching page: article text on tag pages or a caption span on an individual post/reel page, when available. It will not navigate, click, or scroll. Do not use logged-in/private pages. "
        "Automated access may be restricted by Instagram's terms; only continue if authorized. The app does not verify authorization or check that the headed browser is logged out. You can cancel or use `instagram paste` for manual public text."
    )
    response = await cl.AskUserMessage(content=disclosure, timeout=180).send()
    raw_urls = (response.get("output") or "") if response else ""
    urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    if not urls:
        await cl.Message(content="No URLs supplied. Nothing was fetched.").send()
        return
    if len(urls) > 5:
        await cl.Message(content="Please supply at most five URLs per snapshot. Nothing was fetched.").send()
        return
    if not keyword:
        response = await cl.AskUserMessage(
            content="Enter one keyword or phrase to match in the visible text (for example: `budget planning`).",
            timeout=180,
        ).send()
        keyword = (response.get("output") or "").strip() if response else ""
    if not keyword:
        await cl.Message(content="No keyword supplied. Nothing was fetched.").send()
        return

    observations: list[dict[str, str]] = []
    errors: list[str] = []
    for url in urls:
        try:
            observations.extend(await asyncio.to_thread(collect_visible_posts, url))
        except (ValueError, RuntimeError) as exc:
            errors.append(f"{url}: {exc}")
        except Exception:
            logger.exception("Unexpected Instagram collection failure")
            errors.append(f"{url}: unexpected local collection error")

    records = _instagram_records(observations)
    if not records:
        detail = "\n".join(f"- {_md(error)}" for error in errors)
        await cl.Message(content="No visible observations were collected. Check that a page with the same Instagram host and path as each supplied URL is already open in a headed tab.\n\n" + detail).send()
        return
    try:
        result = _analyze_instagram_text(records, keyword)
    except ValueError as exc:
        await cl.Message(content=f"Could not analyze the supplied keyword: {_md(exc)}").send()
        return
    report = _render_keyword_insights(
        result,
        datetime.now(timezone.utc),
        source_description="This reflects only visible text exposed by the supplied Instagram page already open in a headed browser tab: article text on tag pages or a caption span on an individual post/reel page, when available.",
    )
    if errors:
        report += "\n\n### URLs not collected\n" + "\n".join(f"- {_md(error)}" for error in errors)
    await cl.Message(content=report).send()


async def _instagram_manual_flow() -> None:
    response = await cl.AskUserMessage(
        content=(
            "Paste up to five public excerpts, one per line, as `https://www.instagram.com/p/.../ | visible text`. "
            "This route analyzes only what you paste and does not access a browser or fetch the URL. Do not paste private or login-only content."
        ), timeout=180,
    ).send()
    raw = (response.get("output") or "") if response else ""
    try:
        records = _manual_instagram_records(raw)
    except ValueError as exc:
        await cl.Message(content=f"Could not use the pasted observations: {_md(exc)}").send()
        return
    if not records:
        await cl.Message(content="No text pasted. Nothing was analyzed.").send()
        return
    response = await cl.AskUserMessage(
        content="Enter one keyword or phrase to match in the pasted text.", timeout=180,
    ).send()
    keyword = (response.get("output") or "").strip() if response else ""
    if not keyword:
        await cl.Message(content="No keyword supplied. Nothing was analyzed.").send()
        return
    result = _analyze_instagram_text(records, keyword)
    await cl.Message(content=_render_keyword_insights(
        result,
        datetime.now(timezone.utc),
        source_description="This reflects only the public excerpts you pasted. The app did not open the URLs or fetch their contents.",
    )).send()


async def _load_path(path: str, label: str, niche: str = "") -> None:
    try:
        records = load_csv(path)
        if not records:
            await cl.Message(content="That CSV contains no usable records. Choose another file or load the example.").send()
            return
        result = analyze(records, niche=niche)
        result["temporal_comparison"] = analyze_temporal_candidates(records)
    except (OSError, ValueError) as exc:
        await cl.Message(content=f"Could not analyze {_md(label)}: {_md(exc)}").send()
        return
    except Exception:
        logger.exception("Unexpected error while analyzing %s", label)
        await cl.Message(content="Analysis failed unexpectedly. Check the CSV format and local app logs, then try again.").send()
        return

    cl.user_session.set("socialrum_records", records)
    cl.user_session.set("socialrum_analysis", result)
    cl.user_session.set("socialrum_source", label)
    await cl.Message(content=f"Loaded **{_md(label)}**.\n\n{_render_analysis(result)}").send()


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("socialrum_records", None)
    cl.user_session.set("socialrum_analysis", None)
    cl.user_session.set("socialrum_profile", None)
    cl.user_session.set("socialrum_active_draft", None)
    cl.user_session.set("socialrum_latest_public_search", None)
    cl.user_session.set(PUBLIC_SEARCH_GENERATION_KEY, 0)
    # Chainlit 2.12 creates the per-session folder with mkdir(exist_ok=True),
    # which assumes its app-level .files directory already exists.
    (Path(__file__).resolve().parent / ".files").mkdir(parents=True, exist_ok=True)
    await ElementSidebar.set_title("Quick guide")
    await ElementSidebar.set_elements([cl.Text(
        content=(
            "QUICK GUIDE\n\n"
            "COLLECT REELS\nClick Collect reels or send collect #finance / collect personal finance. Enter a hashtag or phrase, then choose a readable-record target from 1 to 50 (default 25). Searches use visible Google results and may return fewer readable records. The existing extract command remains capped at five candidates. Searches run one at a time; a new request during an active search is rejected, not queued.\n\n"
            "WHAT THE RESULTS MEAN\nGoogle snippets are unverified context and are excluded from matching. Keyword matches and co-occurring hashtags use only visible text from readable post pages. "
            "The evidence summary shows matching readable posts / readable posts (each post counted once), readable pages / candidates, and posts with observed dates / readable posts. Matching uses visible text only. "
            "Results may be stale, incomplete, or unavailable; they are not an exhaustive search, trend, or popularity report. "
            "Use only public content you are authorized to access; the app does not verify authorization or guarantee the headed browser is logged out.\n\n"
            "SAVE AND COMPARE SAMPLES\nAfter extract, send capture #finance to save the query, candidate URLs, dates, read status, and (when posts were readable) aggregate co-occurring hashtag counts. Captions, snippets, and per-post hashtag associations are not saved. "
            "Compare #finance shows sample counts as x/readable posts. Older snapshots may lack aggregates; truncated tag lists make omitted counts unknown, not zero. This is not trend evidence. Send forget #finance to delete saved snapshots. Capture never repeats a search.\n\n"
            "PROVIDE INSTAGRAM URLS\nSend instagram <keyword>, or instagram urls to provide page URLs and a keyword. A matching page must already be open in a headed tab. "
            "Send instagram paste to analyze public excerpts you provide.\n\n"
            "FIND PUBLIC POSTS\nSend find #finance or find personal finance to build a Google search link for its index. The app does not run that search.\n\n"
            "TRY THE SYNTHETIC DEMO\nSend demo for fictional sample data.\n\n"
            "ANALYZE A CSV\nSend upload. Required columns: posted_at, creator, content.\n\n"
            "MAKE A DRAFT\nSend draft 1, then edit, review, and save.\n\n"
            "Synthetic data is fictional. CSV rankings describe only the supplied sample."
        ),
        language=None,
        mime="text/plain",
    )])
    await cl.Message(
        content=WELCOME_MESSAGE,
        actions=[
            cl.Action(name="prompt_instagram_target_collection", label="Collect reels", payload={}, icon="search"),
            cl.Action(name="prompt_instagram_extraction", label="Search a topic", payload={}, icon="search"),
            cl.Action(name="extract_instagram", label="Extract #finance", payload={"keywords": "#finance"}, icon="search"),
            cl.Action(name="extract_instagram", label="Extract personal finance", payload={"keywords": "personal finance"}, icon="search"),
            cl.Action(name="find_instagram", label="Find #finance", payload={"keywords": "#finance"}, icon="search"),
            cl.Action(name="find_instagram", label="Find personal finance", payload={"keywords": "personal finance"}, icon="search"),
            cl.Action(name="load_demo", label="Run synthetic example", payload={}, icon="play"),
        ],
    ).send()


@cl.action_callback("load_demo")
async def on_load_demo(_action: cl.Action) -> None:
    await _load_demo()


@cl.action_callback("find_instagram")
async def on_find_instagram(action: cl.Action) -> None:
    payload = action.payload or {}
    keywords = payload.get("keywords", "") if isinstance(payload, dict) else ""
    if not isinstance(keywords, str) or not keywords.strip():
        await cl.Message(content="Could not use that search suggestion. Try `find #finance` or `find personal finance`.").send()
        return
    await _send_instagram_search(keywords)


@cl.action_callback("extract_instagram")
async def on_extract_instagram(action: cl.Action) -> None:
    payload = action.payload or {}
    keywords = payload.get("keywords", "") if isinstance(payload, dict) else ""
    if not isinstance(keywords, str) or not keywords.strip():
        await cl.Message(content="Could not use that discovery suggestion. Try `extract #finance` or `extract personal finance`.").send()
        return
    await _send_instagram_extraction(keywords)


@cl.action_callback("prompt_instagram_extraction")
async def on_prompt_instagram_extraction(_action: cl.Action) -> None:
    try:
        await _prompt_instagram_extraction()
    finally:
        # Chainlit 2.12 action callbacks are wrapped with_task=False. AskUserMessage
        # restarts the task after its reply, so explicitly finish it here.
        await cl.context.emitter.task_end()


@cl.action_callback("prompt_instagram_target_collection")
async def on_prompt_instagram_target_collection(_action: cl.Action) -> None:
    try:
        await _prompt_instagram_target_collection()
    finally:
        await cl.context.emitter.task_end()


async def _load_demo() -> None:
    if not SAMPLE_CSV.is_file():
        await cl.Message(content="The bundled example CSV is missing from this installation.").send()
        return
    await _load_path(str(SAMPLE_CSV), "bundled synthetic example")


@cl.on_message
async def on_message(message: cl.Message) -> None:
    text = (message.content or "").strip()
    command = text.lower()
    if command == "demo":
        await _load_demo()
        return
    if command == "instagram urls":
        await _instagram_keyword_flow()
        return
    if command == "instagram paste":
        await _instagram_manual_flow()
        return
    if command == "find" or command.startswith("find "):
        keywords = text[4:].strip()
        if not keywords:
            await cl.Message(content="Use `find #finance` for a hashtag or `find personal finance` for a keyword phrase. This creates a Google index link; the app does not run the search.").send()
            return
        await _send_instagram_search(keywords)
        return
    if command == "extract" or command.startswith("extract "):
        keywords = text[len("extract"):].strip()
        if not keywords:
            await _prompt_instagram_extraction()
        else:
            await _send_instagram_extraction(keywords)
        return
    if command == "collect" or command.startswith("collect "):
        phrase = text[len("collect"):].strip()
        if not phrase:
            await _prompt_instagram_target_collection()
            return
        match = re.fullmatch(r"(.+?)(?:\s+--target(?:-readable)?\s+(\d+))?", phrase, re.IGNORECASE)
        if not match:
            await cl.Message(content="Use `collect <hashtag or phrase> [--target N]`; N must be from 1 to 50 and defaults to 25.").send()
            return
        target = int(match.group(2)) if match.group(2) else 25
        await _send_instagram_target_collection(match.group(1).strip(), target)
        return
    if command == "capture" or command.startswith("capture "):
        keywords = text[len("capture"):].strip()
        if not keywords:
            await cl.Message(content="Use `capture <same hashtag or phrase>` after a matching `extract` result. Capture only saves that result; it does not search again.").send()
            return
        await _capture_latest_search(keywords)
        return
    if command == "compare" or command.startswith("compare "):
        keywords = text[len("compare"):].strip()
        if not keywords:
            await cl.Message(content="Use `compare <hashtag or phrase>` to compare the latest two matching saved samples. This does not search again.").send()
            return
        await _compare_saved_searches(keywords)
        return
    if command == "forget" or command.startswith("forget "):
        keywords = text[len("forget"):].strip()
        if not keywords:
            await cl.Message(content="Use `forget <hashtag or phrase>` to delete all locally saved snapshots for those terms.").send()
            return
        await _forget_saved_searches(keywords)
        return
    if command.startswith("instagram "):
        keyword = text[len("instagram "):].strip()
        if not keyword:
            await cl.Message(content="Use `instagram <keyword>` or `instagram urls`.").send()
            return
        await _instagram_keyword_flow(keyword)
        return
    if command == "upload":
        files = await cl.AskFileMessage(
            content="Choose a CSV export. Required columns: posted_at, creator, content.",
            accept=["text/csv", ".csv"],
            max_size_mb=5,
            timeout=180,
        ).send()
        if not files:
            await cl.Message(content="No file was selected. Send `upload` when you are ready to try again.").send()
            return
        uploaded = files[0]
        await _load_path(uploaded.path, uploaded.name or "uploaded CSV")
        return
    if command == "profile":
        response = await cl.AskUserMessage(
            content="Enter your creator profile as `name | role` (for example: `Neeraj | backend engineer`).",
            timeout=180,
        ).send()
        value = (response.get("output") or "") if response else ""
        parts = [part.strip() for part in value.split("|", maxsplit=1)]
        if len(parts) != 2 or not all(parts):
            await cl.Message(content="Profile not saved. Please provide both values separated by `|`, then send `profile` again.").send()
            return
        cl.user_session.set("socialrum_profile", {"name": parts[0], "role": parts[1]})
        await cl.Message(content=f"Profile saved for **{_md(parts[0])}**. It stays in this chat session.").send()
        return
    if command == "captions":
        await _burn_user_captions()
        return
    if command == "transcribe":
        await _transcribe_user_media()
        return
    if command == "list":
        await _list_saved_drafts()
        return
    if command.startswith("open "):
        await _open_saved_draft(text[5:].strip())
        return
    if command == "edit":
        await _edit_active_draft()
        return
    if command == "review":
        await _set_review_state()
        return
    if command == "save":
        await _save_active_draft()
        return
    if command == "export":
        await _export_active_draft()
        return
    if command.startswith("draft"):
        await _draft_from_command(text)
        return
    await cl.Message(content="Use `demo`, `upload`, `find <hashtag or keywords>`, `extract <hashtag or keywords>`, `capture <same terms>`, `compare <terms>`, `forget <terms>`, `instagram <keyword>`, `instagram urls`, `instagram paste`, `profile`, `draft <number>`, `edit`, `review`, `save`, `list`, `open <id>`, `export`, `captions`, or `transcribe`. Send `demo` to start with synthetic data.").send()


async def _burn_user_captions() -> None:
    await cl.Message(content="Caption rendering uses only a video and SRT/WebVTT captions you provide. It does not transcribe audio.").send()
    videos = await cl.AskFileMessage(
        content="Choose a video file (MP4, MOV, or MKV; maximum 500 MB).",
        accept=["video/mp4", "video/quicktime", "video/x-matroska"],
        max_size_mb=500,
        timeout=300,
    ).send()


    if not videos:
        await cl.Message(content="No video was selected. Send `captions` to try again.").send()
        return
    subtitles = await cl.AskFileMessage(
        content="Choose your UTF-8 SRT or WebVTT caption file (maximum 5 MB).",
        accept={"application/x-subrip": [".srt"], "text/plain": [".srt"], "text/vtt": [".vtt"]},
        max_size_mb=5,
        timeout=180,
    ).send()
    if not subtitles:
        await cl.Message(content="No subtitle file was selected. Send `captions` to try again.").send()
        return
    uploaded_video_path = Path(videos[0].path)
    uploaded_subtitle_path = Path(subtitles[0].path)
    suffix = Path(videos[0].name or "").suffix.lower()
    subtitle_suffix = Path(subtitles[0].name or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv"}:
        await cl.Message(content="Video filename must end in .mp4, .mov, or .mkv.").send()
        return
    if subtitle_suffix not in {".srt", ".vtt"}:
        await cl.Message(content="Subtitle filename must end in .srt or .vtt.").send()
        return
    try:
        if uploaded_subtitle_path.stat().st_size > 5 * 1024 * 1024:
            await cl.Message(content="Subtitle file exceeds the 5 MB limit.").send()
            return
        with uploaded_subtitle_path.open("rb") as subtitle_stream:
            subtitle_bytes = subtitle_stream.read(5 * 1024 * 1024 + 1)
        if len(subtitle_bytes) > 5 * 1024 * 1024:
            await cl.Message(content="Subtitle file exceeds the 5 MB limit.").send()
            return
    except OSError:
        await cl.Message(content="Could not read the uploaded subtitle file.").send()
        return
    output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"captioned-{uuid.uuid4().hex}{suffix}"
    try:
        with tempfile.TemporaryDirectory(prefix="socialrum-caption-inputs-") as temp_dir:
            temp_root = Path(temp_dir)
            staged_video = temp_root / f"video{suffix}"
            staged_video.symlink_to(uploaded_video_path.resolve())
            staged_subtitle = temp_root / f"captions{subtitle_suffix}"
            staged_subtitle.write_bytes(subtitle_bytes)
            rendered = await asyncio.to_thread(burn_captions, staged_video, staged_subtitle, output_path)
    except (ValueError, RuntimeError) as exc:
        await cl.Message(content=f"Could not render captions: {_md(exc)}").send()
        return
    except Exception:
        logger.exception("Unexpected caption rendering failure")
        await cl.Message(content="Caption rendering failed unexpectedly. Check local app logs and try again.").send()
        return
    await cl.Message(
        content="Captions rendered locally. Download the result below; the original video and subtitle file were left intact.",
        elements=[cl.File(name=rendered.name, path=str(rendered))],
    ).send()



async def _transcribe_user_media() -> None:
    default_root = Path.home() / ".local" / "share" / "socialrum-lab" / "whisper"
    cli_setting = os.environ.get("SOCIALRUM_WHISPER_CLI", str(default_root / "bin" / "whisper-cli")).strip()
    model_setting = os.environ.get("SOCIALRUM_WHISPER_MODEL", str(default_root / "models" / "ggml-tiny.en.bin")).strip()
    cli_found = shutil.which(cli_setting)
    whisper_cli = Path(cli_found or cli_setting).expanduser()
    model_path = Path(model_setting).expanduser()
    if not whisper_cli.is_file() or not os.access(whisper_cli, os.X_OK):
        await cl.Message(content=(
            "Local transcription is not configured. Set `SOCIALRUM_WHISPER_CLI` to an already-installed executable "
            "and `SOCIALRUM_WHISPER_MODEL` to an already-present model file. The app will not download a binary or model."
        )).send()
        return
    if not model_path.is_file() or not os.access(model_path, os.R_OK):
        await cl.Message(content=(
            "The configured local Whisper model file is unavailable or unreadable. Set `SOCIALRUM_WHISPER_MODEL` "
            "to an already-present model file; the app will not download one."
        )).send()
        return

    await cl.Message(content=(
        "This uses only the configured local Whisper CLI and model; no cloud service or download is used. "
        "The default tiny.en model is English-focused; a short synthetic smoke sample did not transcribe its simple expected phrase correctly. "
        "Treat output as a rough machine draft: review and correct every word and timestamp. Accuracy for accents, noisy audio, multiple speakers, "
        "custom models, or non-English speech is not established."
    )).send()
    files = await cl.AskFileMessage(
        content="Choose one audio or video file (maximum 100 MB and 10 minutes; supported: WAV, MP3, M4A, AAC, FLAC, OGG, OPUS, MP4, MOV, MKV, WebM). Cancel or wait for timeout to stop before processing.",
        accept={
            "audio/wav": [".wav"], "audio/x-wav": [".wav"],
            "audio/mpeg": [".mp3"], "audio/mp3": [".mp3"], "audio/mp4": [".m4a", ".aac"],
            "audio/x-m4a": [".m4a"], "audio/aac": [".aac"],
            "audio/flac": [".flac"], "audio/x-flac": [".flac"],
            "audio/ogg": [".ogg", ".opus"], "application/ogg": [".ogg", ".opus"],
            "audio/opus": [".opus"], "audio/webm": [".webm"],
            "video/mp4": [".mp4"], "video/quicktime": [".mov"],
            "video/x-matroska": [".mkv"], "video/webm": [".webm"],
        },
        max_size_mb=100,
        timeout=180,
    ).send()
    if not files:
        await cl.Message(content="No media was selected. Nothing was transcribed; send `transcribe` when ready.").send()
        return

    uploaded = files[0]
    media_path = Path(uploaded.path)
    suffix = Path(uploaded.name or "").suffix.lower()
    if suffix not in TRANSCRIBE_SUFFIXES:
        await cl.Message(content="The uploaded filename must use one of the supported audio/video extensions.").send()
        return
    try:
        if media_path.stat().st_size > TRANSCRIBE_MAX_BYTES:
            await cl.Message(content="Media file exceeds the 100 MB limit.").send()
            return
    except OSError:
        await cl.Message(content="Could not read the uploaded media file.").send()
        return

    try:
        with tempfile.TemporaryDirectory(prefix="socialrum-transcribe-input-") as temp_dir:
            staged_media = Path(temp_dir) / f"media{suffix}"
            staged_media.symlink_to(media_path.resolve())
            srt = await asyncio.to_thread(
                transcribe_media,
                staged_media,
                whisper_cli=whisper_cli,
                model_path=model_path,
                timeout_seconds=120,
            )
    except TranscriptionError as exc:
        await cl.Message(content=f"Local transcription failed: {_md(exc)}").send()
        return
    except ValueError as exc:
        await cl.Message(content=f"Media is not supported for local transcription: {_md(exc)}").send()
        return
    except Exception:
        logger.exception("Unexpected local transcription failure")
        await cl.Message(content="Local transcription failed unexpectedly. Check app logs and the configured local CLI/model.").send()
        return

    srt_bytes = srt.encode("utf-8")
    if len(srt_bytes) > 5 * 1024 * 1024:
        await cl.Message(content="Generated subtitles exceeded the 5 MB output limit and were not saved.").send()
        return
    filename = f"transcript-{uuid.uuid4().hex}.srt"
    try:
        output_path = _create_private_download("transcripts", filename, srt_bytes)
    except (OSError, ValueError):
        logger.exception("Could not save generated local transcript")
        await cl.Message(content="Could not save the generated SRT locally. Check app logs and available disk space.").send()
        return
    await cl.Message(
        content=(
            "Generated SRT is ready to download. Review and correct every caption and timestamp before use. "
            "It was saved locally; no content was published."
        ),
        elements=[cl.File(name=filename, path=str(output_path))],
    ).send()

async def _draft_from_command(text: str) -> None:
    tokens = text.split()
    if len(tokens) not in (2, 4) or (len(tokens) == 4 and tokens[2].lower() != "--model"):
        await cl.Message(content="Use `draft <number>` or `draft <number> --model <local-ollama-model>`. No remote model is used.").send()
        return
    try:
        index = int(tokens[1])
    except ValueError:
        await cl.Message(content="The post number must be an integer, such as `draft 1`.").send()
        return

    analysis = cl.user_session.get("socialrum_analysis")
    if not analysis:
        await cl.Message(content="Load data first with `demo` or `upload`.").send()
        return
    outliers = analysis.get("ranked_outliers") or []
    if index < 1 or index > min(len(outliers), 10):
        await cl.Message(content=f"Choose a ranked post from 1 to {min(len(outliers), 10)}.").send()
        return
    profile = cl.user_session.get("socialrum_profile")
    if not profile:
        response = await cl.AskUserMessage(
            content="First, enter your profile as `name | role` (for example: `Neeraj | backend engineer`).",
            timeout=180,
        ).send()
        value = (response.get("output") or "") if response else ""
        parts = [part.strip() for part in value.split("|", maxsplit=1)]
        if len(parts) != 2 or not all(parts):
            await cl.Message(content="I couldn't save that profile. Send `profile` and try again.").send()
            return
        profile = {"name": parts[0], "role": parts[1]}
        cl.user_session.set("socialrum_profile", profile)

    post = outliers[index - 1]
    topic = post.get("pillar") or analysis.get("niche") or "Post performance insight"
    trend = {
        "topic": topic,
        "summary": post.get("content") or post.get("reason") or "A post ranked as an outlier in the supplied dataset.",
        "source": post.get("post_url") or f"User-provided dataset ({cl.user_session.get('socialrum_source') or 'CSV'})",
    }
    model = tokens[3] if len(tokens) == 4 else None
    try:
        result = generate_draft(profile, trend, model=model)
    except (ValueError, RuntimeError) as exc:
        await cl.Message(content=f"Could not generate the draft: {_md(exc)}").send()
        return
    except Exception:
        logger.exception("Unexpected draft generation failure")
        await cl.Message(content="Draft generation failed unexpectedly. Check local app logs and try again.").send()
        return

    mode_label = "deterministic local template" if result.get("mode") == "template" else f"local model ({result.get('model') or 'unspecified'})"
    generation_note = (
        "The template uses the topic and your profile; it does not rewrite the source post."
        if result.get("mode") == "template"
        else "The source post is unverified reference text; review the generated copy before use."
    )
    source = cl.user_session.get("socialrum_source") or "CSV"
    source_id = post.get("post_url") or f"{source}:ranked-post-{index}"
    now = datetime.now(timezone.utc).isoformat()
    draft_record = {
        "created_at": now,
        "updated_at": now,
        "source_ids": [source_id],
        "provenance": {
            "dataset": source,
            "ranked_post": index,
            "source_url": post.get("post_url"),
            "topic": topic,
            "generation": mode_label,
        },
        "content": str(result.get("draft", "")),
        "review_state": "needs_review",
        "template_version": "socialrum-draft-v1",
        "model": result.get("model"),
        "title": f"Draft for ranked post {index}",
    }
    cl.user_session.set("socialrum_active_draft", draft_record)
    await cl.Message(
        content=(
            f"## {_md(draft_record['title'])}\n"
            f"**Generation:** {_md(mode_label)} · **Review:** needs review\n"
            f"**Source:** {_md(source_id)}\n\n"
            f"{_md(generation_note)}\n\n"
            f"{_md(draft_record['content'])}\n\n"
            "Nothing is published. Use `edit` to replace the text, `review` to change its review state, then `save`."
        )
    ).send()


def _active_draft() -> dict | None:
    draft = cl.user_session.get("socialrum_active_draft")
    return draft if isinstance(draft, dict) else None


def _create_private_download(area: str, filename: str, content: bytes) -> Path:
    """Create a private local download without following links or overwriting."""
    if not re.fullmatch(r"[a-z0-9_-]+", area) or Path(filename).name != filename:
        raise ValueError("invalid local download path")
    DRAFT_STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    store_fd = os.open(DRAFT_STORE, directory_flags)
    area_fd = None
    file_fd = None
    created_file = False
    try:
        try:
            os.mkdir(area, mode=0o700, dir_fd=store_fd)
        except FileExistsError:
            pass
        area_fd = os.open(area, directory_flags, dir_fd=store_fd)
        os.fchmod(area_fd, 0o700)
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(filename, file_flags, 0o600, dir_fd=area_fd)
        created_file = True
        with os.fdopen(file_fd, "wb") as stream:
            file_fd = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return DRAFT_STORE / area / filename
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        if area_fd is not None and created_file:
            try:
                os.unlink(filename, dir_fd=area_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if area_fd is not None:
            os.close(area_fd)
        os.close(store_fd)


def _create_export_file(draft_id: str, markdown: str) -> Path:
    """Create a private Markdown export with an opaque, unique filename."""
    if not re.fullmatch(r"[0-9a-f]{32}", draft_id):
        raise ValueError("invalid draft ID for export")
    filename = f"{draft_id}-{uuid.uuid4().hex}.md"
    return _create_private_download("exports", filename, markdown.encode("utf-8"))


async def _list_saved_drafts() -> None:
    try:
        drafts = list_drafts(DRAFT_STORE)
    except (OSError, ValueError):
        logger.exception("Could not list local drafts")
        await cl.Message(content="Could not read the local draft library. Check the app logs for the file that needs attention.").send()
        return
    if not drafts:
        await cl.Message(content="No saved drafts yet. Generate one with `draft <number>`, then send `save`.").send()
        return
    lines = ["## Saved drafts", "Send `open <id>` to reopen one."]
    for draft in drafts[:30]:
        lines.append(f"- `{_md(draft['id'])}` · {_md(draft.get('title') or 'Untitled draft')} · {_md(draft.get('review_state') or 'needs_review')} · {_md(draft.get('updated_at') or '')}")
    await cl.Message(content="\n".join(lines)).send()


async def _open_saved_draft(draft_id: str) -> None:
    if not draft_id or len(draft_id) != 32:
        await cl.Message(content="Use `open <32-character draft id>`. Get an ID with `list`.").send()
        return
    try:
        draft = load_draft(draft_id, DRAFT_STORE)
    except FileNotFoundError:
        await cl.Message(content="That draft was not found. Send `list` to see saved draft IDs.").send()
        return
    except (OSError, ValueError):
        logger.exception("Could not open local draft %s", draft_id)
        await cl.Message(content="Could not open that draft. Its local record may be invalid; check the app logs.").send()
        return
    cl.user_session.set("socialrum_active_draft", draft)
    await _show_active_draft()


async def _show_active_draft() -> None:
    draft = _active_draft()
    if not draft:
        await cl.Message(content="No active draft. Generate one or use `open <id>`.").send()
        return
    provenance = draft.get("provenance") or "Not recorded"
    await cl.Message(content=(
        f"## {_md(draft.get('title') or 'Draft')}\n"
        f"**ID:** {_md(draft.get('id') or 'unsaved')} · **Review:** {_md(draft.get('review_state') or 'needs_review')}\n"
        f"**Source/provenance:** {_md(provenance)}\n\n"
        f"{_md(draft.get('content') or '')}\n\n"
        "Use `edit`, `review`, `save`, or `export`."
    )).send()


async def _edit_active_draft() -> None:
    draft = _active_draft()
    if not draft:
        await cl.Message(content="Generate a draft or use `open <id>` before editing.").send()
        return
    response = await cl.AskUserMessage(content="Paste the complete replacement draft text. The current version remains unchanged if you send an empty response.", timeout=600).send()
    content = (response.get("output") or "") if response else ""
    if not content.strip():
        await cl.Message(content="No changes made.").send()
        return
    draft["content"] = content
    cl.user_session.set("socialrum_active_draft", draft)
    await cl.Message(content="Draft edited in this session. Review it, then send `save` to persist locally.").send()
    await _show_active_draft()


async def _set_review_state() -> None:
    draft = _active_draft()
    if not draft:
        await cl.Message(content="Generate a draft or use `open <id>` before changing its review state.").send()
        return
    response = await cl.AskUserMessage(content="Set review state to `needs_review`, `reviewed`, or `approved` (local bookkeeping only; nothing is published).", timeout=180).send()
    state = ((response.get("output") or "") if response else "").strip().lower()
    if state not in {"needs_review", "reviewed", "approved"}:
        await cl.Message(content="Review state unchanged. Choose `needs_review`, `reviewed`, or `approved`.").send()
        return
    draft["review_state"] = state
    cl.user_session.set("socialrum_active_draft", draft)
    await cl.Message(content=f"Review state set to **{_md(state)}** in this session. Send `save` to persist it.").send()


async def _save_active_draft() -> None:
    draft = _active_draft()
    if not draft:
        await cl.Message(content="Generate a draft or use `open <id>` before saving.").send()
        return
    try:
        saved = save_draft(
            draft,
            DRAFT_STORE,
            draft_id=draft.get("id"),
            overwrite=bool(draft.get("id")),
            expected_updated_at=draft.get("updated_at") if draft.get("id") else None,
        )
    except DraftConflictError:
        await cl.Message(content="This saved draft changed or was removed since you opened it. Your in-session edits are preserved; use `open <id>` to load the current saved version before continuing.").send()
        return
    except (OSError, ValueError, FileExistsError):
        logger.exception("Could not save local draft")
        await cl.Message(content="Draft could not be saved locally. Check the app logs; your in-session edit is still available.").send()
        return
    cl.user_session.set("socialrum_active_draft", saved)
    await cl.Message(content=f"Saved locally. Draft ID: `{_md(saved['id'])}` · Review: **{_md(saved.get('review_state'))}**. Use `list` to find it later.").send()


async def _export_active_draft() -> None:
    draft = _active_draft()
    if not draft:
        await cl.Message(content="Generate a draft or use `open <id>` before exporting.").send()
        return
    if not draft.get("id"):
        await cl.Message(content="Save the draft first so its export includes a persistent ID and timestamps.").send()
        return
    try:
        markdown = export_markdown(draft)
        export_path = _create_export_file(draft["id"], markdown)
    except (OSError, ValueError):
        logger.exception("Could not export local draft")
        await cl.Message(content="Could not create the local Markdown export. Check the app logs.").send()
        return
    await cl.Message(content="Markdown export is ready to download. It includes the source/provenance and review state; nothing is published.", elements=[cl.File(name=export_path.name, path=str(export_path))]).send()
