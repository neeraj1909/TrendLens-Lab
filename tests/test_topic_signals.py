import json

import pytest

from socialrum.topic_signals import MAX_SIGNALS, analyze_topic_signals


def test_counts_distinct_read_posts_case_insensitively_and_excludes_explicit_query_tag():
    result = analyze_topic_signals(
        [
            {"source_url": "https://example.test/p/1/", "status": "read", "post_text": "#Coffee #AI #ai"},
            {"source_url": "https://example.test/p/2/?utm_source=x", "status": "read", "post_text": "#coffee #Climate"},
            {"source_url": "https://example.test/p/3/", "status": "read", "post_text": "#coffee #Coffee"},
            {"source_url": "https://example.test/p/4/", "status": "read", "post_text": "#coffee #Climate"},
        ],
        "#Coffee",
    )

    assert result["candidate_count"] == 4
    assert result["read_post_count"] == 4
    assert result["signals"] == [
        {"hashtag": "#Climate", "post_count": 2},
        {"hashtag": "#AI", "post_count": 1},
    ]
    assert result["truncated"] is False
    assert result["interpretation"] == "sample_level_cooccurrence_counts_only"
    assert "incomplete, stale, or biased" in result["data_note"]
    assert json.loads(json.dumps(result)) == result


def test_skips_missing_or_inaccessible_posts_and_deduplicates_same_source_url():
    result = analyze_topic_signals(
        [
            {"source_url": "https://example.test/p/1/", "status": "read", "post_text": "#AI #Data"},
            {"source_url": "https://example.test/p/1/?tracking=yes", "status": "read", "post_text": "#AI #Cloud"},
            {"source_url": "https://example.test/p/2/", "status": "unavailable", "post_text": "#AI #WronglyCounted"},
            {"source_url": "https://example.test/p/3/", "post_text": "  "},
            None,
        ],
        "AI",
    )

    assert result["candidate_count"] == 5
    assert result["read_post_count"] == 1
    # A plain phrase is not treated as the searched hashtag.
    assert result["signals"] == [
        {"hashtag": "#AI", "post_count": 1},
        {"hashtag": "#Data", "post_count": 1},
    ]


def test_unicode_letters_numbers_and_combining_marks_are_supported():
    result = analyze_topic_signals(
        [{"status": "read", "post_text": "#café #नमस्ते #e\u0301clair #東京 #123 #_tag #tag-suffix"}],
        "#topic",
    )

    assert {item["hashtag"] for item in result["signals"]} == {
        "#café", "#नमस्ते", "#e\u0301clair", "#東京", "#123", "#_tag", "#tag",
    }


def test_empty_or_unreadable_sample_is_explicit_and_safe():
    result = analyze_topic_signals(
        [{"status": "unavailable"}, {"post_text": None}, {"post_text": ""}],
        "#topic",
    )

    assert result["candidate_count"] == 3
    assert result["read_post_count"] == 0
    assert result["signals"] == []
    assert result["truncated"] is False
    assert "No readable post text" in result["data_note"]


def test_signal_output_is_capped_and_limit_is_validated():
    text = " ".join(f"#tag{i}" for i in range(MAX_SIGNALS + 5))
    result = analyze_topic_signals([{"status": "read", "post_text": text}], "#query", limit=MAX_SIGNALS)

    assert len(result["signals"]) == MAX_SIGNALS
    assert result["truncated"] is True
    exact_limit = analyze_topic_signals(
        [{"status": "read", "post_text": " ".join(f"#tag{i}" for i in range(MAX_SIGNALS))}],
        "#query", limit=MAX_SIGNALS,
    )
    assert len(exact_limit["signals"]) == MAX_SIGNALS
    assert exact_limit["truncated"] is False
    with pytest.raises(ValueError, match="limit"):
        analyze_topic_signals([], "query", limit=MAX_SIGNALS + 1)
    with pytest.raises(ValueError, match="limit"):
        analyze_topic_signals([], "query", limit=True)


def test_explicit_query_hashtag_is_excluded_but_plain_words_are_not():
    hashtags = [{"status": "read", "post_text": "#Climate #Action #climate"}]

    explicit = analyze_topic_signals(hashtags, "#climate action")
    phrase = analyze_topic_signals(hashtags, "climate action")

    assert [item["hashtag"] for item in explicit["signals"]] == ["#Action"]
    assert [item["hashtag"] for item in phrase["signals"]] == ["#Action", "#Climate"]
