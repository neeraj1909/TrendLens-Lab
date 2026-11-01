"""Keyword-led, evidence-grounded ideas from locally supplied post records.

This module describes only the text and metadata present in its inputs. It does
not estimate popularity, discover trends, or infer audience response.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


def _contains(text: str, keyword: str) -> bool:
    """Match a keyword phrase case-insensitively, respecting word edges."""
    pattern = re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", re.IGNORECASE)
    return pattern.search(text) is not None


def _excerpt(content: str, keyword: str, limit: int = 240) -> str:
    """Return a short sentence containing the match, retaining source wording."""
    match = re.search(
        r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", content, re.IGNORECASE
    )
    if match is None:
        return content[:limit].strip()
    start = max(content.rfind(mark, 0, match.start()) for mark in (".", "!", "?", "\n")) + 1
    endings = [position for mark in (".", "!", "?", "\n")
               if (position := content.find(mark, match.end())) >= 0]
    end = min(endings) + 1 if endings else len(content)
    sentence = content[start:end].strip()
    if len(sentence) > limit:
        left = max(0, match.start() - start - limit // 2)
        sentence = sentence[left:left + limit].strip()
        if left:
            sentence = "…" + sentence
        if start + left + limit < end:
            sentence += "…"
    return sentence


def _idea(excerpt: str) -> tuple[str, str]:
    """Offer an explicitly proposed angle, without adding factual claims."""
    if "?" in excerpt:
        idea = (f"Idea: Respond to the question in the observed text ({excerpt}) "
                "with a clearly labeled personal example or practical answer.")
    else:
        idea = (f"Idea: Unpack the specific point in the observed text ({excerpt}) "
                "with a clearly labeled personal example or explanation.")
    punchline = f'Source hook (quoted; see linked source): “{excerpt}”'
    return idea, punchline


def _draft_suggestion(excerpt: str) -> str:
    """Build an attributed post template with only user-supplied claims."""
    return (
        f'Post template (edit before posting): I saw this point in the linked '
        f'post: “{excerpt}” My experience: [describe one step you personally '
        'took]. [Include an outcome only if you can verify it.]'
    )


def analyze_keyword_insights(
    records: Iterable[Mapping[str, Any]], keywords: Iterable[str]
) -> dict[str, Any]:
    """Find literal keyword matches and return their source provenance.

    ``records`` use the shape returned by :func:`socialrum.data.load_csv`:
    required ``content`` plus optional ``posted_at``, ``creator``, ``post_url``
    and ``platform``. Missing dates and creator labels remain ``None``. Keyword matching is case-insensitive and uses whole-word
    edges, including multiword phrases. A URL can match independently of the
    visible post text and is always returned exactly as supplied.

    Idea and punchline strings are creative prompts grounded in matched source
    excerpts; they are not claims about performance, popularity, or audience.
    No metrics or relative ranking are calculated.
    """
    normalized_keywords: list[str] = []
    for index, raw in enumerate(keywords, start=1):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"keyword {index} must be a non-empty string")
        keyword = raw.strip()
        if keyword.casefold() not in {item.casefold() for item in normalized_keywords}:
            normalized_keywords.append(keyword)

    normalized_records: list[dict[str, Any]] = []
    for index, source in enumerate(records, start=1):
        if not isinstance(source, Mapping):
            raise ValueError(f"record {index} must be a mapping")
        record = dict(source)
        value = record.get("content")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"record {index}: content must be a non-empty string")
        for field in ("posted_at", "creator"):
            value = record.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"record {index}: {field} must be a string or None")
        normalized_records.append(record)

    insights: list[dict[str, Any]] = []
    for keyword in normalized_keywords:
        matches: list[dict[str, Any]] = []
        for record in normalized_records:
            content = record["content"].strip()
            url = record.get("post_url")
            locations = []
            if _contains(content, keyword):
                locations.append("visible_text")
            if isinstance(url, str) and _contains(url, keyword):
                locations.append("post_url")
            if not locations:
                continue
            excerpt = _excerpt(content, keyword) if "visible_text" in locations else ""
            matches.append({
                "posted_at": record["posted_at"].strip() if record.get("posted_at") else None,
                "creator": record["creator"].strip() if record.get("creator") else None,
                "platform": record.get("platform"),
                "post_url": url,
                "matched_in": locations,
                "excerpt": excerpt,
            })

        exemplars = [match["excerpt"] for match in matches if match["excerpt"]]
        if exemplars:
            idea, punchline = _idea(exemplars[0])
            draft_suggestion = _draft_suggestion(exemplars[0])
        else:
            idea = None
            punchline = None
            draft_suggestion = None
        insights.append({
            "keyword": keyword,
            "match_count": len(matches),
            "matches": matches,
            "idea": idea,
            "punchline": punchline,
            "draft_suggestion": draft_suggestion,
        })

    return {
        "source_kind": "user_supplied_observations",
        "interpretation": "literal_keyword_matches_not_popularity_or_trend_analysis",
        "record_count": len(normalized_records),
        "keywords": insights,
        "data_note": "Matches reflect only supplied visible text and URLs; absence of a match is not evidence of absence elsewhere.",
    }
