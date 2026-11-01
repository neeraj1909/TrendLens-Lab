#!/usr/bin/env python3
"""Run one user-authorized, bounded CDP search for public Instagram posts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from socialrum.search_connector import search_and_collect_public_posts  # noqa: E402
from socialrum.discovery import normalize_instagram_phrase  # noqa: E402
from socialrum.keyword_insights import analyze_keyword_insights  # noqa: E402


DEFAULT_TARGET_READABLE = 25
MAX_TARGET_READABLE = 50
MAX_ATTEMPTS = 100
MAX_PAGES = 10
MAX_ELAPSED_SECONDS = 600


def _bounded_int(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return parse


def _add_sample_summary(result: dict, keyword: str, target_readable: int = DEFAULT_TARGET_READABLE) -> None:
    """Summarize only readable collected text; snippets and URLs are excluded."""
    candidates = result.get("candidates")
    candidates = candidates if isinstance(candidates, list) else []
    readable = [
        item for item in candidates
        if isinstance(item, dict) and item.get("status") == "read"
        and isinstance(item.get("post_text"), str) and item["post_text"].strip()
    ]
    phrase = normalize_instagram_phrase(keyword)
    matches = sum(
        bool(analyze_keyword_insights([{"content": item["post_text"]}], [phrase])
             ["keywords"][0]["match_count"])
        for item in readable
    )
    attempted = result.get("attempted_count")
    if not isinstance(attempted, int) or isinstance(attempted, bool):
        attempted = len(candidates)
    reported_readable = result.get("readable_count")
    if not isinstance(reported_readable, int) or isinstance(reported_readable, bool):
        reported_readable = len(readable)
    result["sample_summary"] = {
        "search_phrase": phrase,
        "candidate_count": len(candidates),
        "attempted_candidate_count": attempted,
        "target_readable_count": target_readable,
        "successfully_read_nonempty_text_count": reported_readable,
        "target_met": reported_readable >= target_readable,
        "literal_visible_text_match_count": matches,
        "match_denominator": reported_readable,
        "elapsed_seconds": result.get("elapsed_seconds"),
        "pages_visited": result.get("pages_visited"),
        "stop_reason": result.get("stop_reason"),
        "note": (
            "Matches use the existing case-insensitive whole-word phrase semantics on collected visible text only; "
            "Google snippets and URLs are excluded. These bounded sample counts do not represent platform totals, "
            "popularity, or trends."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seek readable Instagram reel records through bounded, Google-indexed discovery using the local CDP CLI.",
        epilog=(
            "Discovery uses Google, not Instagram's live search. Results may be stale or incomplete. "
            "Public visibility does not itself authorize automated collection."
        ),
    )
    parser.add_argument("keyword", help="One phrase or hashtag, for example 'personal finance' or '#finance'")
    parser.add_argument(
        "--target-readable", type=_bounded_int(1, MAX_TARGET_READABLE), metavar="N",
        default=DEFAULT_TARGET_READABLE,
        help="Readable nonempty reel captions to seek (1-50; default: 25; may return fewer)",
    )
    parser.add_argument(
        "--max-attempts", "--limit", dest="max_attempts", type=_bounded_int(1, MAX_ATTEMPTS),
        metavar="N", default=MAX_ATTEMPTS,
        help="Maximum unique reel candidates to attempt (1-100; default: 100; --limit is a compatibility alias)",
    )
    parser.add_argument(
        "--max-pages", type=_bounded_int(1, MAX_PAGES), metavar="N", default=MAX_PAGES,
        help="Maximum visible Google result pages to inspect (1-10; default: 10)",
    )
    parser.add_argument(
        "--time-budget-seconds", type=_bounded_int(1, MAX_ELAPSED_SECONDS), metavar="SECONDS",
        default=MAX_ELAPSED_SECONDS,
        help="Maximum elapsed time for this search (1-600 seconds; default: 600)",
    )
    content_scope = parser.add_mutually_exclusive_group()
    content_scope.add_argument(
        "--reels-only", dest="reels_only", action="store_true", default=True,
        help="Search for and inspect reels only (default)",
    )
    content_scope.add_argument(
        "--include-posts", dest="reels_only", action="store_false",
        help="Also include Instagram post URLs for a mixed sample",
    )
    parser.add_argument(
        "--recent-days", type=int, choices=range(1, 366), metavar="DAYS",
        help="Ask Google to filter results to the past 1-365 days (publication dates may be unknown)",
    )
    args = parser.parse_args()
    try:
        result = search_and_collect_public_posts(
            args.keyword, target_readable=args.target_readable, max_attempts=args.max_attempts,
            max_pages=args.max_pages, max_elapsed_seconds=args.time_budget_seconds,
            reels_only=args.reels_only, recent_days=args.recent_days,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Search failed: {exc}", file=sys.stderr)
        return 1
    _add_sample_summary(result, args.keyword, args.target_readable)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Target completion is a successful stop; preserve shortfall JSON with status 2.
    return 0 if result["sample_summary"]["target_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
