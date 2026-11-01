"""Build user-run public search links for Instagram posts and reels.

This module only formats a URL. It does not contact a search engine or
Instagram, inspect results, or claim that results are complete or current.
"""

from __future__ import annotations

from urllib.parse import urlencode

MAX_KEYWORD_CHARS = 200
_SEARCH_ENDPOINT = "https://www.google.com/search"


def normalize_instagram_phrase(keywords: str) -> str:
    """Return the effective phrase used by the Instagram Google search.

    Whitespace is collapsed, quotes and backslashes are removed, and other
    characters including a leading hashtag are preserved.
    """
    if not isinstance(keywords, str):
        raise ValueError("keywords must be a non-empty string")
    phrase = " ".join(keywords.split()).strip()
    phrase = phrase.replace('"', "").replace("\\", "")
    if not phrase:
        raise ValueError("keywords must be a non-empty string")
    if len(phrase) > MAX_KEYWORD_CHARS:
        raise ValueError(f"keywords must be at most {MAX_KEYWORD_CHARS} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in phrase):
        raise ValueError("keywords cannot contain control characters")
    return phrase


def build_instagram_search(keywords: str) -> dict[str, str]:
    """Return a clickable Google query for public Instagram post/reel URLs.

    The phrase is quoted as a single search phrase. URL parameter encoding
    keeps the phrase as data in the fixed Google search URL.
    """
    phrase = normalize_instagram_phrase(keywords)

    query = f'(site:instagram.com/p/ OR site:instagram.com/reel/) "{phrase}"'
    return {
        "source_kind": "user_run_public_search",
        "search_engine": "Google",
        "query": query,
        "url": f"{_SEARCH_ENDPOINT}?{urlencode({'q': query})}",
        "provenance_note": (
            "This link opens a search-engine index query for public Instagram post/reel URLs. "
            "You run the search yourself; results are not a live Instagram feed, may be "
            "incomplete or stale, and were not fetched or verified by this app."
        ),
    }
