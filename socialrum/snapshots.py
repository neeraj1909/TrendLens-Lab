"""Private local history of bounded, user-run keyword search observations.

Snapshots describe only what was observed in a particular search. They do not
measure Instagram-wide activity, posting growth, ranking velocity, or popularity.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_RECORD_BYTES = 64_000
_MAX_CANDIDATES = 5
_MAX_TEXT = 2_000
_STATUSES = {"read", "unreadable", "not_attempted"}
_MAX_TAGS = 20


def normalize_terms(terms: str) -> str:
    """Normalize case and whitespace while preserving punctuation such as #."""
    if not isinstance(terms, str):
        raise ValueError("terms must be a non-empty string")
    normalized = " ".join(terms.split()).casefold()
    if (
        not normalized
        or len(normalized) > 200
        or any(ord(ch) < 32 for ch in normalized)
    ):
        raise ValueError("terms must be 1 to 200 printable characters")
    return normalized


def _text(value: Any, field: str, *, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{field} is too long or contains control characters")
    return value


def _query_key(query: str) -> str:
    return " ".join(_text(query, "query").split()).casefold()


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def _observed_date(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("observed_date must be an ISO date or None")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("observed_date must be an ISO date") from exc
    return value


def _normalize_candidates(candidates: Any) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    if len(candidates) > _MAX_CANDIDATES:
        raise ValueError(f"at most {_MAX_CANDIDATES} candidates may be saved")
    result = []
    seen = set()
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"candidate {index} must be a mapping")
        if set(item) - {"url", "status", "observed_date"}:
            raise ValueError(f"candidate {index} contains unsupported fields")
        url = _text(item.get("url"), f"candidate {index} url", limit=4_000)
        if not (url.startswith("https://") or url.startswith("http://")):
            raise ValueError(f"candidate {index} url must be an HTTP(S) URL")
        if url in seen:
            raise ValueError(f"candidate {index} duplicates a URL")
        seen.add(url)
        status = item.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError(
                f"candidate {index} status must be one of {sorted(_STATUSES)}"
            )
        result.append(
            {
                "url": url,
                "status": status,
                "observed_date": _observed_date(item.get("observed_date")),
            }
        )
    return result


def _normalize_topic_signals(value: Any, read_count: int) -> dict[str, Any]:
    """Validate a bounded aggregate without storing any post-to-tag links."""
    if not isinstance(value, dict) or set(value) != {
        "read_denominator",
        "tags",
        "truncated",
    }:
        raise ValueError(
            "topic_signals must contain read_denominator, tags, and truncated"
        )
    denominator = value["read_denominator"]
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        raise ValueError("topic_signals read_denominator must be an integer")
    if denominator < 1 or denominator > _MAX_CANDIDATES or denominator != read_count:
        raise ValueError(
            "topic_signals denominator must equal the successfully read candidate count"
        )
    if not isinstance(value["truncated"], bool):
        raise ValueError("topic_signals truncated must be a boolean")
    tags = value["tags"]
    if not isinstance(tags, list) or len(tags) > _MAX_TAGS:
        raise ValueError(
            f"topic_signals tags must be a list of at most {_MAX_TAGS} items"
        )
    normalized = []
    seen = set()
    for index, item in enumerate(tags, start=1):
        if not isinstance(item, dict) or set(item) != {"tag", "count"}:
            raise ValueError(f"topic signal {index} must contain only tag and count")
        tag = item["tag"]
        if (
            not isinstance(tag, str)
            or len(tag) > 81
            or not tag.startswith("#")
            or len(tag) == 1
            or not all(
                char == "_" or unicodedata.category(char)[0] in {"L", "N", "M"}
                for char in tag[1:]
            )
        ):
            raise ValueError(f"topic signal {index} tag must be a hashtag")
        if tag.casefold() in seen:
            raise ValueError(f"topic signal {index} duplicates a tag")
        seen.add(tag.casefold())
        count = item["count"]
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= denominator
        ):
            raise ValueError(
                f"topic signal {index} count must be between 1 and the denominator"
            )
        normalized.append({"tag": tag, "count": count})
    return {
        "read_denominator": denominator,
        "tags": normalized,
        "truncated": value["truncated"],
    }


class _Lock:
    def __init__(self, directory: Path):
        self.path = directory / ".snapshots.lock"

    def __enter__(self):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(self.path, flags, 0o600)
        os.fchmod(self.fd, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _directory(store_dir: str | os.PathLike[str]) -> Path:
    directory = Path(store_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    return directory


def _path(directory: Path, snapshot_id: str) -> Path:
    if not isinstance(snapshot_id, str) or not _ID.fullmatch(snapshot_id):
        raise ValueError("snapshot id must be 32 lowercase hexadecimal characters")
    return directory / f"{snapshot_id}.json"


def save_snapshot(
    terms: str,
    query: str,
    source: str,
    source_version: str,
    candidates: list[dict[str, Any]],
    store_dir: str | os.PathLike[str],
    *,
    observed_at: str | None = None,
    topic_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save one immutable search observation, never overwriting an existing ID."""
    identifier = uuid.uuid4().hex
    directory = _directory(store_dir)
    normalized_candidates = _normalize_candidates(candidates)
    record = {
        "id": identifier,
        "terms": normalize_terms(terms),
        "query": _text(query, "query"),
        "source": _text(source, "source", limit=200),
        "source_version": _text(source_version, "source_version", limit=200),
        "captured_at": _timestamp(
            observed_at or datetime.now(timezone.utc).isoformat(), "captured_at"
        ),
        "candidates": normalized_candidates,
    }
    if topic_signals is not None:
        read_count = sum(item["status"] == "read" for item in normalized_candidates)
        record["topic_signals"] = _normalize_topic_signals(topic_signals, read_count)
    payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > _MAX_RECORD_BYTES:
        raise ValueError(f"snapshot exceeds {_MAX_RECORD_BYTES} byte limit")
    with _Lock(directory):
        fd, temporary = tempfile.mkstemp(
            prefix=f".{identifier}.", suffix=".tmp", dir=directory
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, _path(directory, identifier), follow_symlinks=False)
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return record


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"snapshot {path.stem} is not a regular file")
    if path.stat().st_size > _MAX_RECORD_BYTES:
        raise ValueError(f"snapshot {path.stem} exceeds {_MAX_RECORD_BYTES} byte limit")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"snapshot {path.stem} contains malformed JSON") from exc
    if not isinstance(record, dict) or record.get("id") != path.stem:
        raise ValueError(f"snapshot {path.stem} has an invalid record")
    try:
        base_fields = {
            "id",
            "terms",
            "query",
            "source",
            "source_version",
            "captured_at",
            "candidates",
        }
        if set(record) not in (base_fields, base_fields | {"topic_signals"}):
            raise ValueError("record contains missing or unsupported fields")
        if normalize_terms(record.get("terms")) != record["terms"]:
            raise ValueError("terms are not normalized")
        _text(record.get("query"), "query")
        _text(record.get("source"), "source", limit=200)
        _text(record.get("source_version"), "source_version", limit=200)
        _timestamp(record.get("captured_at"), "captured_at")
        candidates = _normalize_candidates(record.get("candidates"))
        if "topic_signals" in record:
            read_count = sum(item["status"] == "read" for item in candidates)
            _normalize_topic_signals(record["topic_signals"], read_count)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"snapshot {path.stem} has an invalid record: {exc}") from exc
    return record


def list_snapshots(
    terms: str,
    store_dir: str | os.PathLike[str],
    *,
    query: str | None = None,
    source: str | None = None,
    source_version: str | None = None,
) -> list[dict[str, Any]]:
    """Return matching observations newest first; malformed files are reported."""
    directory = Path(store_dir)
    if not directory.exists():
        return []
    key = normalize_terms(terms)
    query_key = _query_key(query) if query is not None else None
    records = [_load(path) for path in directory.glob("*.json")]
    return sorted(
        (
            r
            for r in records
            if r["terms"] == key
            and (query_key is None or _query_key(r["query"]) == query_key)
            and (source is None or r["source"] == source)
            and (source_version is None or r["source_version"] == source_version)
        ),
        key=lambda r: datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00")),
        reverse=True,
    )


def compare_latest_snapshots(
    terms: str,
    query: str,
    source: str,
    source_version: str,
    store_dir: str | os.PathLike[str],
) -> dict[str, Any]:
    """Compare two matching searches as local link recurrence only."""
    records = list_snapshots(
        terms, store_dir, query=query, source=source, source_version=source_version
    )[:2]
    if len(records) < 2:
        return {"status": "insufficient", "reason": "fewer_than_two_matching_snapshots"}
    previous, latest = records[1], records[0]
    if not previous["candidates"] or not latest["candidates"]:
        return {"status": "insufficient", "reason": "snapshot_has_no_candidates"}
    for record in (previous, latest):
        if not any(item["status"] == "read" for item in record["candidates"]):
            return {
                "status": "insufficient",
                "reason": "snapshot_has_no_successfully_read_post",
            }
    old = {item["url"] for item in previous["candidates"]}
    new = {item["url"] for item in latest["candidates"]}

    def summary(record: dict[str, Any]) -> dict[str, Any]:
        counts = {status: 0 for status in sorted(_STATUSES)}
        for candidate in record["candidates"]:
            counts[candidate["status"]] += 1
        return {
            "captured_at": record["captured_at"],
            "candidate_count": len(record["candidates"]),
            "successfully_read_count": counts["read"],
            "status_counts": counts,
        }

    if "topic_signals" in previous and "topic_signals" in latest:
        topic_comparison: dict[str, Any] = {
            "status": "available",
            "interpretation": "aggregate co-hashtag observations in these saved samples only; not trend or popularity evidence",
        }
        for label, record in (("previous", previous), ("latest", latest)):
            signals = record["topic_signals"]
            denominator = signals["read_denominator"]
            topic_comparison[label] = {
                "read_denominator": denominator,
                "truncated": signals["truncated"],
                "omission_note": (
                    "Tags absent from this top-20 list are unknown, not zero."
                    if signals["truncated"]
                    else None
                ),
                "tags": [
                    {**item, "raw": f"{item['count']}/{denominator}"}
                    for item in signals["tags"]
                ],
            }
    else:
        topic_comparison = {
            "status": "unavailable",
            "reason": "aggregate_missing_from_one_or_more_snapshots",
        }

    return {
        "status": "compared_local_observations",
        "terms": normalize_terms(terms),
        "query": query,
        "source": source,
        "source_version": source_version,
        "previous": summary(previous),
        "latest": summary(latest),
        "recurring_urls": sorted(old & new),
        "newly_observed_urls": sorted(new - old),
        "no_longer_observed_urls": sorted(old - new),
        "topic_signals": topic_comparison,
        "interpretation": "search-result observations only; not Instagram-wide trends, posting growth, ranking velocity, or popularity",
    }


def delete_snapshot(snapshot_id: str, store_dir: str | os.PathLike[str]) -> bool:
    """Delete one snapshot by its validated opaque ID; return whether it existed."""
    directory = Path(store_dir)
    if not directory.exists():
        _path(directory, snapshot_id)  # validate ID even when the directory is absent
        return False
    target = _path(directory, snapshot_id)
    with _Lock(directory):
        if target.is_symlink():
            raise ValueError("refusing to delete a symlink snapshot")
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        return True


def delete_snapshots(terms: str, store_dir: str | os.PathLike[str]) -> int:
    """Delete every snapshot for normalized terms and return the count."""
    directory = Path(store_dir)
    if not directory.exists():
        return 0
    key = normalize_terms(terms)
    removed = 0
    with _Lock(directory):
        for path in directory.glob("*.json"):
            record = _load(path)
            if record["terms"] == key:
                path.unlink()
                removed += 1
    return removed
