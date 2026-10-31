"""Safe local CSV ingestion and descriptive analysis for SocialRum.

The module works only on observations supplied by the user or bundled demo CSV.
It never contacts Instagram or any other service. Missing measurements remain
missing; the analysis does not estimate or infer unavailable metrics.

Public API
----------
``load_csv(path)`` returns normalized record dictionaries with ``posted_at``,
``creator``, ``content``, optional text fields (``platform``, ``post_url``,
``pillar``), and optional integer metrics (``views``, ``likes``, ``comments``,
``shares``, ``saves``, ``followers``). Dates use ISO ``YYYY-MM-DD``.

``analyze(records, niche='')`` returns JSON-serializable ``niche``,
``record_count``, ``creator_stats``, ``content_pillars``, ``ranked_outliers``,
and ``data_notes``. Engagement rate uses the sum of explicitly available
interactions divided by views; records without observed views or interactions
have no rate. A post is ranked only against at least three same-creator,
same-pillar peer rates with the same observed interaction metric set and when
its rate is at least 1.5 times their median.
"""

from __future__ import annotations

import csv
import math
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
MAX_COLUMNS = 32
REQUIRED = {"posted_at", "creator", "content"}
METRICS = {"views", "likes", "comments", "shares", "saves", "followers"}
TEXT_FIELDS = {"platform", "post_url", "pillar"}
FIELDS = REQUIRED | METRICS | TEXT_FIELDS
ALIASES = {
    "date": "posted_at", "publish_date": "posted_at", "published_at": "posted_at",
    "handle": "creator", "account": "creator", "username": "creator",
    "caption": "content", "text": "content", "url": "post_url",
    "content_pillar": "pillar", "impressions": "views",
    "follower_count": "followers", "likes_count": "likes",
    "comments_count": "comments", "shares_count": "shares", "saves_count": "saves",
}


def _header_name(raw: str) -> str:
    name = raw.strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(name, name)


def _date_text(value: str, row_number: int) -> str:
    value = value.strip()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"row {row_number}: posted_at must be a real ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"row {row_number}: posted_at must use YYYY-MM-DD")
    return value


def load_csv(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read a bounded local CSV and normalize supported columns.

    Required columns are posted_at/date, creator/handle, and content/caption.
    Raises ValueError for malformed, oversized, or structurally unsafe input;
    filesystem errors retain their normal Python exceptions.
    """
    csv_path = Path(path)
    size = csv_path.stat().st_size
    if size > MAX_CSV_BYTES:
        raise ValueError(f"CSV exceeds {MAX_CSV_BYTES} byte limit")
    records: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        try:
            raw_headers = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV is empty; a header row is required") from exc
        if len(raw_headers) > MAX_COLUMNS:
            raise ValueError(f"CSV has more than {MAX_COLUMNS} columns")
        headers = [_header_name(header) for header in raw_headers]
        if any(not header for header in headers) or len(set(headers)) != len(headers):
            raise ValueError("CSV headers must be non-empty and unique after normalization")
        unknown = set(headers) - FIELDS
        if unknown:
            raise ValueError(f"unsupported CSV columns: {', '.join(sorted(unknown))}")
        missing = REQUIRED - set(headers)
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

        try:
            for row_number, values in enumerate(reader, start=2):
                if row_number > MAX_ROWS + 1:
                    raise ValueError(f"CSV exceeds {MAX_ROWS} data rows")
                if len(values) != len(headers):
                    raise ValueError(f"row {row_number}: expected {len(headers)} cells, got {len(values)}")
                row = dict(zip(headers, values))
                posted_at = _date_text(row["posted_at"], row_number)
                creator = row["creator"].strip()
                content = row["content"].strip()
                if not creator or not content:
                    raise ValueError(f"row {row_number}: creator and content cannot be blank")
                record: dict[str, Any] = {
                    "posted_at": posted_at, "creator": creator, "content": content,
                    "platform": None, "post_url": None, "pillar": None,
                }
                for field in TEXT_FIELDS:
                    if field in row and row[field].strip():
                        record[field] = row[field].strip()
                for field in METRICS:
                    raw = row.get(field, "").strip()
                    if not raw:
                        record[field] = None
                        continue
                    try:
                        number = float(raw)
                    except ValueError as exc:
                        raise ValueError(f"row {row_number}: {field} must be a non-negative integer") from exc
                    if not math.isfinite(number) or number < 0 or not number.is_integer():
                        raise ValueError(f"row {row_number}: {field} must be a finite non-negative integer")
                    record[field] = int(number)
                records.append(record)
        except csv.Error as exc:
            raise ValueError(f"malformed CSV: {exc}") from exc
    return records


def _record_rate(record: dict[str, Any]) -> float | None:
    interactions = [record.get(key) for key in ("likes", "comments", "shares", "saves")]
    known = [value for value in interactions if value is not None]
    denominator = record.get("views")
    if not known or denominator is None or denominator <= 0:
        return None
    return sum(known) / denominator


def _interaction_coverage(record: dict[str, Any]) -> tuple[str, ...]:
    """Return the exact observed interaction components used in this rate."""
    return tuple(key for key in ("likes", "comments", "shares", "saves")
                 if record.get(key) is not None)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def analyze(records: Iterable[dict[str, Any]], niche: str = "") -> dict[str, Any]:
    """Summarize observations and rank unusually high observed engagement.

    Supplied records are checked for required text/date fields and valid known
    metrics. The returned data uses plain dict/list/str/int/float/None values.
    """
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(records, start=1):
        if not isinstance(source, dict):
            raise ValueError(f"record {index} must be a mapping")
        item = dict(source)
        missing = REQUIRED - item.keys()
        if missing:
            raise ValueError(f"record {index} missing required fields: {', '.join(sorted(missing))}")
        item["posted_at"] = _date_text(str(item["posted_at"]), index)
        for field in ("creator", "content"):
            item[field] = str(item[field]).strip()
            if not item[field]:
                raise ValueError(f"record {index}: {field} cannot be blank")
        item.setdefault("pillar", "Uncategorized")
        item["pillar"] = str(item["pillar"] or "Uncategorized").strip() or "Uncategorized"
        item.setdefault("post_url", None)
        for field in METRICS:
            value = item.get(field)
            if value is None:
                continue
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0 or not float(value).is_integer()):
                raise ValueError(f"record {index}: {field} must be a finite non-negative integer or None")
        normalized.append(item)

    rates = [_record_rate(item) for item in normalized]
    creators: dict[str, list[int]] = {}
    pillars: dict[str, list[int]] = {}
    peers: dict[tuple[str, str, tuple[str, ...]], list[float]] = {}
    for index, item in enumerate(normalized):
        creators.setdefault(item["creator"], []).append(index)
        pillars.setdefault(item["pillar"], []).append(index)
        if rates[index] is not None:
            key = (item["creator"], item["pillar"], _interaction_coverage(item))
            peers.setdefault(key, []).append(rates[index])

    def summary(label: str, indices: list[int]) -> dict[str, Any]:
        covered_rates = [(rates[i], _interaction_coverage(normalized[i]))
                         for i in indices if rates[i] is not None]
        coverage_sets = sorted({coverage for _, coverage in covered_rates})
        known_rates = [rate for rate, _ in covered_rates]
        views = [normalized[i].get("views") for i in indices]
        return {
            label: normalized[indices[0]][label], "post_count": len(indices),
            "total_views": sum(value for value in views if value is not None) if any(value is not None for value in views) else None,
            "avg_engagement_rate": (sum(known_rates) / len(known_rates)
                                     if known_rates and len(coverage_sets) == 1 else None),
            "avg_engagement_coverage": {
                "rate_post_count": len(known_rates),
                "metric_sets": [list(coverage) for coverage in coverage_sets],
            },
        }

    creator_stats = [summary("creator", indices) for _, indices in sorted(creators.items())]
    content_pillars = [summary("pillar", indices) for _, indices in sorted(pillars.items())]
    ranked: list[dict[str, Any]] = []
    for index, item in enumerate(normalized):
        rate = rates[index]
        coverage = _interaction_coverage(item)
        group_rates = peers.get((item["creator"], item["pillar"], coverage), [])
        if rate is None or len(group_rates) < 4:  # focal post plus at least three peers
            continue
        peer_rates = [candidate for j, candidate in enumerate(rates)
                      if j != index and normalized[j]["creator"] == item["creator"]
                      and normalized[j]["pillar"] == item["pillar"] and candidate is not None
                      and _interaction_coverage(normalized[j]) == coverage]
        if len(peer_rates) < 3:
            continue
        baseline = _median(peer_rates)
        if baseline <= 0 or rate < baseline * 1.5:
            continue
        ranked.append({
            "posted_at": item["posted_at"], "creator": item["creator"], "pillar": item["pillar"],
            "content": item["content"], "post_url": item.get("post_url"),
            "engagement_rate": rate, "peer_median": baseline,
            "benchmark_count": len(peer_rates), "lift": rate / baseline,
            "interaction_metrics": list(coverage),
            "reason": "Observed engagement rate is at least 1.5x the same-creator, same-pillar peer median with matching interaction metric coverage.",
        })
    ranked.sort(key=lambda item: (-item["lift"], item["posted_at"], item["creator"]))
    notes = ["Analysis uses only the supplied manual/imported observations; no live platform data is fetched.",
             "Missing measurements remain unknown. Engagement rates use observed interactions divided by views only; follower counts are never used as a substitute denominator.",
             "Outlier comparisons require the same observed interaction metrics and at least three comparable peer posts; results are descriptive, not causal."]
    return {
        "niche": niche.strip(), "record_count": len(normalized),
        "creator_stats": creator_stats, "content_pillars": content_pillars,
        "ranked_outliers": ranked, "data_notes": notes,
    }


def analyze_temporal_candidates(
    records: Iterable[dict[str, Any]], *, window_days: int = 7,
    min_posts: int = 3, min_creators: int = 2,
) -> dict[str, Any]:
    """Compare pillar post counts across adjacent windows in supplied records.

    The recent inclusive window ends on the newest observed date; the baseline
    is the immediately preceding window of equal length. This is retrospective
    frequency analysis, not live trend velocity. Exact duplicate observations
    are counted once. One-off metric snapshots are deliberately not compared.
    """
    for name, value in (("window_days", window_days), ("min_posts", min_posts),
                        ("min_creators", min_creators)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    supplied = list(records)
    analyze(supplied)  # Reuse the public input validation and date normalization rules.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    metric_fields = ("views", "likes", "comments", "shares", "saves", "followers")
    for item in supplied:
        identity = (
            str(item["posted_at"]), str(item["creator"]).strip(),
            str(item["content"]).strip(),
            str(item.get("pillar") or "Uncategorized").strip() or "Uncategorized",
            item.get("post_url"),
            *(item.get(field) for field in metric_fields),
        )
        if identity not in seen:
            seen.add(identity)
            normalized = dict(item)
            normalized["posted_at"] = identity[0]
            normalized["creator"] = identity[1]
            normalized["pillar"] = identity[3]
            normalized["post_url"] = item.get("post_url")
            unique.append(normalized)

    if not unique:
        return {
            "source_kind": "user_supplied_observations",
            "interpretation": "retrospective_window_comparison_not_live_trend_or_velocity",
            "anchor_date": None,
            "window_days": window_days,
            "minimums": {"posts_per_window": min_posts, "distinct_creators_per_window": min_creators},
            "date_coverage": {"start": None, "end": None, "record_count": 0,
                              "distinct_creator_count": 0},
            "comparisons": [],
        }

    dates = [date.fromisoformat(item["posted_at"]) for item in unique]
    anchor = max(dates)
    recent_end = anchor
    recent_start = anchor.fromordinal(anchor.toordinal() - window_days + 1)
    baseline_end = recent_start.fromordinal(recent_start.toordinal() - 1)
    baseline_start = baseline_end.fromordinal(baseline_end.toordinal() - window_days + 1)
    pillars = sorted({item.get("pillar") or "Uncategorized" for item in unique})

    def window_summary(pillar: str, start: date, end: date) -> dict[str, Any]:
        matching = [item for item in unique
                    if (item.get("pillar") or "Uncategorized") == pillar
                    and start.isoformat() <= item["posted_at"] <= end.isoformat()]
        matching.sort(key=lambda item: (item["posted_at"], item["creator"], item.get("post_url") or ""))
        return {
            "start": start.isoformat(), "end": end.isoformat(),
            "post_count": len(matching),
            "distinct_creator_count": len({item["creator"] for item in matching}),
            "source_records": [
                {"posted_at": item["posted_at"], "creator": item["creator"],
                 "post_url": item.get("post_url")}
                for item in matching
            ],
        }

    comparisons = []
    for pillar in pillars:
        recent = window_summary(pillar, recent_start, recent_end)
        baseline = window_summary(pillar, baseline_start, baseline_end)
        if baseline["post_count"] == 0:
            status = "zero_baseline"
        elif (recent["post_count"] < min_posts
              or recent["distinct_creator_count"] < min_creators):
            status = "insufficient_recent_data"
        elif (baseline["post_count"] < min_posts
              or baseline["distinct_creator_count"] < min_creators):
            status = "insufficient_baseline_data"
        else:
            status = "comparable"
        comparisons.append({
            "candidate": pillar,
            "candidate_kind": "content_pillar",
            "recent_window": recent,
            "baseline_window": baseline,
            "status": status,
            "post_count_change": (recent["post_count"] - baseline["post_count"]
                                  if status == "comparable" else None),
        })

    return {
        "source_kind": "user_supplied_observations",
        "interpretation": "retrospective_window_comparison_not_live_trend_or_velocity",
        "anchor_date": anchor.isoformat(),
        "window_days": window_days,
        "minimums": {"posts_per_window": min_posts, "distinct_creators_per_window": min_creators},
        "date_coverage": {
            "start": min(dates).isoformat(), "end": max(dates).isoformat(),
            "record_count": len(unique),
            "distinct_creator_count": len({item["creator"] for item in unique}),
        },
        "comparisons": comparisons,
    }
