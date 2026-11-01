import json

import pytest

from socialrum.keyword_insights import analyze_keyword_insights


def test_keyword_matches_are_literal_case_insensitive_and_include_source_provenance():
    result = analyze_keyword_insights(
        [
            {
                "posted_at": "2026-10-30T10:00:00+00:00",
                "creator": "@maker",
                "platform": "Instagram",
                "post_url": "https://example.test/post/1",
                "content": "A practical guide to budgeting for freelancers. Start with one expense.",
                "likes": 5000,
            },
            {
                "posted_at": None,
                "creator": "@accountant",
                "post_url": "https://example.test/freelancer-budgeting",
                "content": "Budgeting helps organize monthly bills.",
            },
        ],
        ["freelancers", "budgeting", "freelance"],
    )

    freelancers, budgeting, freelance = result["keywords"]
    assert result["source_kind"] == "user_supplied_observations"
    assert result["record_count"] == 2
    assert freelancers["match_count"] == 1
    assert freelancers["matches"][0] == {
        "posted_at": "2026-10-30T10:00:00+00:00",
        "creator": "@maker",
        "platform": "Instagram",
        "post_url": "https://example.test/post/1",
        "matched_in": ["visible_text"],
        "excerpt": "A practical guide to budgeting for freelancers.",
    }
    assert budgeting["match_count"] == 2
    assert budgeting["matches"][1]["posted_at"] is None
    assert budgeting["matches"][1]["matched_in"] == ["visible_text", "post_url"]
    assert freelance["match_count"] == 0  # whole-word edge, not a substring
    assert freelance["idea"] is None
    assert json.loads(json.dumps(result)) == result


def test_idea_and_punchline_are_explicit_prompts_grounded_in_observed_excerpt():
    result = analyze_keyword_insights(
        [{"creator": "@one", "content": "What does a fair budget look like?"}],
        ["budget"],
    )

    insight = result["keywords"][0]
    assert "What does a fair budget look like?" in insight["matches"][0]["excerpt"]
    assert insight["idea"].startswith("Idea: Respond to the question in the observed text")
    assert "personal example" in insight["idea"]
    assert insight["punchline"] == (
        "Source hook (quoted; see linked source): “What does a fair budget look like?”"
    )
    assert insight["draft_suggestion"] == (
        'Post template (edit before posting): I saw this point in the linked '
        'post: “What does a fair budget look like?” My experience: [describe '
        'one step you personally took]. [Include an outcome only if you can verify it.]'
    )
    assert insight["matches"][0]["posted_at"] is None


def test_url_only_match_preserves_provenance_without_fabricating_excerpt_or_idea():
    result = analyze_keyword_insights(
        [{
            "creator": "@one",
            "post_url": "https://example.test/finance-guide",
            "content": "A general guide for getting organized.",
        }],
        ["finance"],
    )

    insight = result["keywords"][0]
    assert insight["match_count"] == 1
    assert insight["matches"][0]["matched_in"] == ["post_url"]
    assert insight["matches"][0]["excerpt"] == ""
    assert insight["idea"] is None
    assert insight["punchline"] is None
    assert insight["draft_suggestion"] is None


def test_does_not_use_engagement_metrics_or_claim_trends():
    result = analyze_keyword_insights(
        [
            {"creator": "@one", "content": "Remote work routines", "likes": 1000000},
            {"creator": "@two", "content": "Remote work routines", "likes": 0},
        ],
        ["remote work"],
    )

    assert result["interpretation"] == "literal_keyword_matches_not_popularity_or_trend_analysis"
    assert result["keywords"][0]["match_count"] == 2
    assert "trend" not in result["keywords"][0]["idea"].lower()
    assert all("likes" not in match for match in result["keywords"][0]["matches"])
    assert "likes" not in result


def test_deduplicates_keywords_case_insensitively_and_validates_inputs():
    result = analyze_keyword_insights([], ["finance", " FINANCE "])

    assert [item["keyword"] for item in result["keywords"]] == ["finance"]
    assert result["keywords"][0]["match_count"] == 0
    assert result["keywords"][0]["idea"] is None
    with pytest.raises(ValueError, match="keyword 1"):
        analyze_keyword_insights([], [""])
    with pytest.raises(ValueError, match="content"):
        analyze_keyword_insights([{"creator": "@one"}], ["finance"])
