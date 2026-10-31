import csv
import json
from pathlib import Path

import pytest

from socialrum.data import analyze, analyze_temporal_candidates, load_csv


def test_load_csv_normalizes_aliases_and_preserves_missing_metrics(tmp_path):
    path = tmp_path / "posts.csv"
    path.write_text(
        "date,handle,caption,likes,views,pillar\n"
        "2026-10-01,@maker,Useful post,12,300,Education\n"
        "2026-10-02,@maker,Unknown views,, ,Education\n",
        encoding="utf-8",
    )

    records = load_csv(path)

    assert records[0]["posted_at"] == "2026-10-01"
    assert records[0]["creator"] == "@maker"
    assert records[0]["content"] == "Useful post"
    assert records[0]["likes"] == 12
    assert records[1]["likes"] is None
    assert records[1]["views"] is None


@pytest.mark.parametrize(
    "csv_text",
    [
        "creator,content\nuser,post\n",
        "posted_at,creator,content,likes\n2026-10-01,u,p,-2\n",
        "posted_at,creator,content,views\n2026-02-30,u,p,1\n",
        "posted_at,creator,content,likes\n2026-10-01,u,p,NaN\n",
        "posted_at,creator,content,caption\n2026-10-01,u,p,p\n",
    ],
)
def test_load_csv_rejects_invalid_input(tmp_path, csv_text):
    path = tmp_path / "bad.csv"
    path.write_text(csv_text, encoding="utf-8")

    with pytest.raises(ValueError):
        load_csv(path)


def test_analyze_returns_serializable_ranked_summaries_without_inventing_metrics():
    records = [
        {"posted_at": f"2026-10-0{i}", "creator": "a", "content": f"post {i}",
         "pillar": "Education", "views": views, "likes": likes,
         "comments": 0, "shares": 0, "saves": 0}
        for i, (views, likes) in enumerate(((100, 10), (100, 11), (100, 9), (100, 80)), 1)
    ]
    records.append({"posted_at": "2026-10-05", "creator": "b", "content": "missing", "pillar": "News"})

    result = analyze(records, niche="home coffee")

    assert result["niche"] == "home coffee"
    assert result["record_count"] == 5
    assert result["creator_stats"][0]["creator"] == "a"
    assert result["content_pillars"]
    assert result["ranked_outliers"][0]["engagement_rate"] == pytest.approx(0.8)
    assert result["ranked_outliers"][0]["benchmark_count"] == 3
    assert result["ranked_outliers"][0]["interaction_metrics"] == ["likes", "comments", "shares", "saves"]
    assert result["data_notes"]
    assert json.loads(json.dumps(result)) == result
    assert not any("engagement_rate" in item for item in result["ranked_outliers"] if item["creator"] == "b")


def test_empty_and_too_small_peer_groups_do_not_claim_outliers():
    result = analyze([
        {"posted_at": "2026-10-01", "creator": "a", "content": "one", "pillar": "Tips", "views": 100, "likes": 50},
        {"posted_at": "2026-10-02", "creator": "a", "content": "two", "pillar": "Tips", "views": 100, "likes": 50},
    ])

    assert result["ranked_outliers"] == []
    assert analyze([])["record_count"] == 0


def test_follower_count_is_not_used_as_a_substitute_for_views():
    result = analyze([{
        "posted_at": "2026-10-01", "creator": "a", "content": "post",
        "likes": 10, "followers": 100,
    }])

    assert result["creator_stats"][0]["avg_engagement_rate"] is None


def test_outlier_baseline_requires_matching_interaction_metric_coverage():
    records = [
        {"posted_at": f"2026-10-0{i}", "creator": "a", "content": f"post {i}",
         "pillar": "Tips", "views": 100, "likes": likes}
        for i, likes in enumerate((10, 10, 10), 1)
    ]
    records.append({
        "posted_at": "2026-10-04", "creator": "a", "content": "different coverage",
        "pillar": "Tips", "views": 100, "likes": 90, "comments": 10,
    })

    result = analyze(records)

    assert result["ranked_outliers"] == []


def test_summary_suppresses_average_when_observed_rates_have_mixed_coverage():
    records = [
        {"posted_at": "2026-10-01", "creator": "a", "content": "likes only",
         "pillar": "Tips", "views": 100, "likes": 10},
        {"posted_at": "2026-10-02", "creator": "a", "content": "likes and comments",
         "pillar": "Tips", "views": 100, "likes": 10, "comments": 20},
        {"posted_at": "2026-10-03", "creator": "a", "content": "missing rate",
         "pillar": "Tips", "views": 0, "likes": 4},
    ]

    result = analyze(records)

    for summary in (result["creator_stats"][0], result["content_pillars"][0]):
        assert summary["avg_engagement_rate"] is None
        assert summary["avg_engagement_coverage"] == {
            "rate_post_count": 2,
            "metric_sets": [["likes"], ["likes", "comments"]],
        }


def test_summary_preserves_average_for_homogeneous_metric_coverage():
    result = analyze([
        {"posted_at": "2026-10-01", "creator": "a", "content": "one",
         "pillar": "Tips", "views": 100, "likes": 10},
        {"posted_at": "2026-10-02", "creator": "a", "content": "two",
         "pillar": "Tips", "views": 200, "likes": 40},
    ])

    summary = result["creator_stats"][0]
    assert summary["avg_engagement_rate"] == pytest.approx(0.15)
    assert summary["avg_engagement_coverage"] == {
        "rate_post_count": 2,
        "metric_sets": [["likes"]],
    }


@pytest.mark.parametrize("field", ["views", "likes", "comments", "shares", "saves", "followers"])
def test_analyze_rejects_fractional_metric_counts(field):
    with pytest.raises(ValueError, match=field):
        analyze([{
            "posted_at": "2026-10-01", "creator": "a", "content": "post",
            field: 1.5,
        }])


def _dated_post(day, creator, pillar="Education", content="post", post_url=None):
    return {"posted_at": day, "creator": creator, "pillar": pillar,
            "content": content, "post_url": post_url}


def test_temporal_comparison_uses_adjacent_inclusive_windows_and_reports_provenance():
    records = [
        _dated_post("2026-10-01", "a"), _dated_post("2026-10-02", "b"),
        _dated_post("2026-10-03", "a"), _dated_post("2026-10-04", "b"),
        _dated_post("2026-10-08", "a", post_url="https://example.test/new-a"),
        _dated_post("2026-10-10", "b", post_url="https://example.test/new-b"),
        _dated_post("2026-10-11", "c", post_url="https://example.test/new-c"),
    ]

    result = analyze_temporal_candidates(records)

    assert result["source_kind"] == "user_supplied_observations"
    assert result["interpretation"] == "retrospective_window_comparison_not_live_trend_or_velocity"
    assert result["date_coverage"] == {
        "start": "2026-10-01", "end": "2026-10-11", "record_count": 7,
        "distinct_creator_count": 3,
    }
    comparison = result["comparisons"][0]
    assert comparison["recent_window"]["start"] == "2026-10-05"
    assert comparison["recent_window"]["end"] == "2026-10-11"
    assert comparison["recent_window"]["post_count"] == 3
    assert comparison["recent_window"]["distinct_creator_count"] == 3
    assert comparison["recent_window"]["source_records"][0] == {
        "posted_at": "2026-10-08", "creator": "a",
        "post_url": "https://example.test/new-a",
    }
    assert comparison["baseline_window"]["start"] == "2026-09-28"
    assert comparison["baseline_window"]["end"] == "2026-10-04"
    assert comparison["baseline_window"]["post_count"] == 4
    assert comparison["status"] == "comparable"
    assert comparison["post_count_change"] == -1


def test_temporal_comparison_reports_zero_and_insufficient_baselines_without_lift():
    records = [
        _dated_post("2026-10-08", "a", "Zero"),
        _dated_post("2026-10-09", "b", "Zero"),
        _dated_post("2026-10-10", "c", "Zero"),
        _dated_post("2026-10-01", "a", "Small"),
        _dated_post("2026-10-08", "a", "Small"),
        _dated_post("2026-10-09", "b", "Small"),
        _dated_post("2026-10-10", "c", "Small"),
    ]

    result = analyze_temporal_candidates(records)
    by_candidate = {item["candidate"]: item for item in result["comparisons"]}

    assert by_candidate["Zero"]["status"] == "zero_baseline"
    assert by_candidate["Zero"]["baseline_window"]["post_count"] == 0
    assert by_candidate["Zero"]["post_count_change"] is None
    assert by_candidate["Small"]["status"] == "insufficient_baseline_data"
    assert by_candidate["Small"]["post_count_change"] is None
    assert all("lift" not in item for item in result["comparisons"])


def test_temporal_comparison_deduplicates_exact_rows_and_requires_recent_creator_coverage():
    duplicate = _dated_post("2026-10-08", "a")
    records = [duplicate, duplicate.copy(), _dated_post("2026-10-09", "a"),
               _dated_post("2026-10-10", "a"),
               _dated_post("2026-10-01", "a"), _dated_post("2026-10-02", "b"),
               _dated_post("2026-10-03", "b")]

    result = analyze_temporal_candidates(records)

    assert result["date_coverage"]["record_count"] == 6
    comparison = result["comparisons"][0]
    assert comparison["recent_window"]["post_count"] == 3
    assert comparison["recent_window"]["distinct_creator_count"] == 1
    assert comparison["status"] == "insufficient_recent_data"
    assert comparison["post_count_change"] is None


def test_temporal_comparison_empty_data_has_explicit_unknown_coverage():
    result = analyze_temporal_candidates([])

    assert result["anchor_date"] is None
    assert result["date_coverage"] == {
        "start": None, "end": None, "record_count": 0, "distinct_creator_count": 0,
    }
    assert result["comparisons"] == []


def test_bundled_demo_supports_multiple_ranked_examples_and_comparable_windows():
    sample_csv = Path(__file__).resolve().parents[1] / "data" / "sample.csv"
    records = load_csv(sample_csv)

    result = analyze(records, niche="creator education")
    ranked_content = {post["content"] for post in result["ranked_outliers"]}
    assert result["record_count"] == 20
    assert ranked_content == {
        "[DEMO] The one-page launch checklist readers saved",
        "[DEMO] Three community questions, answered",
    }
    assert all(post["benchmark_count"] >= 3 for post in result["ranked_outliers"])

    temporal = analyze_temporal_candidates(records)
    assert temporal["date_coverage"]["start"] == "2026-10-15"
    assert temporal["date_coverage"]["end"] == "2026-10-28"
    comparable = {item["candidate"]: item for item in temporal["comparisons"]
                  if item["status"] == "comparable"}
    assert set(comparable) == {"Education", "Behind the scenes", "Community"}
    assert comparable["Behind the scenes"]["baseline_window"]["post_count"] == 3
    assert comparable["Behind the scenes"]["recent_window"]["post_count"] == 4
    assert comparable["Behind the scenes"]["post_count_change"] == 1
