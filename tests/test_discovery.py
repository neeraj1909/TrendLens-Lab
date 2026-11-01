from urllib.parse import parse_qs, urlsplit

import pytest

from socialrum.discovery import (
    MAX_KEYWORD_CHARS,
    build_instagram_search,
    normalize_instagram_phrase,
)


def test_builds_clickable_encoded_search_link_with_clear_provenance():
    result = build_instagram_search("  budget   planning & savings  ")

    assert result["source_kind"] == "user_run_public_search"
    assert result["search_engine"] == "Google"
    assert "site:instagram.com/p/ OR site:instagram.com/reel/" in result["query"]
    assert '"budget planning & savings"' in result["query"]
    parsed = urlsplit(result["url"])
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https", "www.google.com", "/search"
    )
    assert parse_qs(parsed.query) == {"q": [result["query"]]}
    assert "You run the search yourself" in result["provenance_note"]
    assert "not a live Instagram feed" in result["provenance_note"]
    assert "not fetched or verified" in result["provenance_note"]


def test_user_text_stays_inside_the_fixed_search_phrase():
    result = build_instagram_search('cats" OR site:example.com\\path')

    assert result["query"].endswith('"cats OR site:example.compath"')
    assert urlsplit(result["url"]).netloc == "www.google.com"


def test_normalize_instagram_phrase_matches_search_builder_sanitization():
    raw = '  "personal\\   finance"  '

    assert normalize_instagram_phrase(raw) == "personal finance"
    assert '"personal finance"' in build_instagram_search(raw)["query"]
    assert normalize_instagram_phrase("#Finance") == "#Finance"


@pytest.mark.parametrize("value", ["", "  ", "\x00", None, 42])
def test_rejects_empty_or_invalid_keywords(value):
    with pytest.raises(ValueError):
        build_instagram_search(value)


def test_bounds_keyword_length():
    assert build_instagram_search("x" * MAX_KEYWORD_CHARS)["query"]
    with pytest.raises(ValueError, match="at most"):
        build_instagram_search("x" * (MAX_KEYWORD_CHARS + 1))
