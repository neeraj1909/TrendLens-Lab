"""Descriptive co-occurring hashtag counts from a small collected post sample.

Counts are based only on distinct posts whose visible text was successfully
read. They are not measures of popularity, engagement, representativeness, or
trends.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

MAX_SIGNALS = 20


def _is_hashtag_char(char: str) -> bool:
    return char == "_" or unicodedata.category(char)[0] in {"L", "N", "M"}


def _is_word_char(char: str) -> bool:
    return char == "_" or unicodedata.category(char)[0] in {"L", "N", "M"}


def _hashtags(text: str) -> list[str]:
    """Extract Unicode hashtags, retaining their first-seen spelling."""
    found: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "#" or index + 1 >= len(text):
            index += 1
            continue
        if index and (_is_word_char(text[index - 1]) or text[index - 1] == "#"):
            index += 1
            continue
        start = index + 1
        end = start
        while end < len(text) and _is_hashtag_char(text[end]):
            end += 1
        if end > start:
            found.append(text[index:end])
            index = end
        else:
            index += 1
    return found


def _source_key(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            # Search results may carry tracking parameters or fragments for the
            # same post. Paths stay case-sensitive because URLs can be so.
            path = parts.path.rstrip("/")
            return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, "", ""))
    except ValueError:
        pass
    return url


def analyze_topic_signals(
    candidates: Iterable[Mapping[str, Any]], query: str, *, limit: int = 10
) -> dict[str, Any]:
    """Count co-occurring hashtags in distinct, readable collected posts.

    Candidates use the shape returned by ``search_and_collect_public_posts``.
    Only a literal hashtag explicitly written in ``query`` is excluded; a
    plain-text phrase is not guessed to be a hashtag. Missing or inaccessible
    candidate content is skipped. Repeated hashtags in one post count once.
    """
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if (not isinstance(limit, int) or isinstance(limit, bool)
            or not 1 <= limit <= MAX_SIGNALS):
        raise ValueError(f"limit must be between 1 and {MAX_SIGNALS}")

    rows = list(candidates)
    excluded = {tag.casefold() for tag in _hashtags(query)}
    seen_sources: set[str] = set()
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    read_count = 0

    for candidate in rows:
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("post_text")
        if (candidate.get("status") != "read"
                or not isinstance(content, str) or not content.strip()):
            continue
        source_key = _source_key(candidate.get("source_url"))
        if source_key is not None:
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
        read_count += 1

        tags_in_post: dict[str, str] = {}
        for tag in _hashtags(content):
            key = tag.casefold()
            if key not in excluded:
                tags_in_post.setdefault(key, tag)
        for key, tag in tags_in_post.items():
            counts[key] += 1
            display.setdefault(key, tag)

    signals = [
        {"hashtag": display[key], "post_count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
    truncated = len(counts) > limit
    note = (
        "Counts describe only successfully read text among the collected search-result candidates. "
        "This small convenience sample may be incomplete, stale, or biased by search ranking and "
        "post accessibility; it does not estimate platform-wide frequency, popularity, engagement, "
        "or trends."
    )
    if read_count == 0:
        note += " No readable post text was available, so no co-occurrence conclusion can be drawn."
    elif not signals:
        note += " No co-occurring hashtags were found in the readable text after excluding the query hashtag."
    return {
        "source_kind": "successfully_read_collected_posts",
        "query": query,
        "candidate_count": len(rows),
        "read_post_count": read_count,
        "signals": signals,
        "truncated": truncated,
        "interpretation": "sample_level_cooccurrence_counts_only",
        "data_note": note,
    }
