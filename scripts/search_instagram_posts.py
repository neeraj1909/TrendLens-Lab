#!/usr/bin/env python3
"""Run one user-authorized, bounded CDP search for public Instagram posts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from socialrum.search_connector import MAX_CANDIDATES, MAX_SEARCH_CANDIDATES, search_and_collect_public_posts  # noqa: E402
from socialrum.discovery import normalize_instagram_phrase  # noqa: E402
from socialrum.keyword_insights import analyze_keyword_insights  # noqa: E402


def _add_sample_summary(result: dict, keyword: str) -> None:
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
    result["sample_summary"] = {
        "search_phrase": phrase,
        "candidate_count": len(candidates),
        "successfully_read_nonempty_text_count": len(readable),
        "literal_visible_text_match_count": matches,
        "match_denominator": len(readable),
        "note": (
            "Matches use the existing case-insensitive whole-word phrase semantics on collected visible text only; "
            "Google snippets and URLs are excluded. These bounded sample counts do not represent platform totals, "
            "popularity, or trends."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and read up to 20 Instagram post/reel candidates using the local CDP CLI.",
        epilog=(
            "Discovery uses Google, not Instagram's live search. Results may be stale or incomplete. "
            "Public visibility does not itself authorize automated collection."
        ),
    )
    parser.add_argument("keyword", help="One phrase or hashtag, for example 'personal finance' or '#finance'")
    parser.add_argument(
        "--limit", type=int, choices=range(1, MAX_SEARCH_CANDIDATES + 1), default=MAX_CANDIDATES,
        help="Maximum number of candidates to inspect (1-20; default: 5)",
    )
    parser.add_argument("--reels-only", action="store_true", help="Search for and inspect reel URLs only")
    parser.add_argument(
        "--recent-days", type=int, choices=range(1, 366), metavar="DAYS",
        help="Ask Google to filter results to the past 1-365 days (publication dates may be unknown)",
    )
    args = parser.parse_args()
    try:
        result = search_and_collect_public_posts(
            args.keyword, max_candidates=args.limit, reels_only=args.reels_only,
            recent_days=args.recent_days,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Search failed: {exc}", file=sys.stderr)
        return 1
    _add_sample_summary(result, args.keyword)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Preserve partial JSON while signaling that collection stopped early.
    return 2 if result.get("stop_reason") else 0


if __name__ == "__main__":
    raise SystemExit(main())
