"""Private, local persistence and Markdown export for reviewed drafts."""

from __future__ import annotations

import fcntl
import html
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_RECORD_BYTES = 1_000_000


class DraftConflictError(ValueError):
    """Raised when an edit was based on an older stored revision."""


class _StoreLock:
    """Small advisory lock shared by threads and processes on this host."""

    def __init__(self, directory: Path):
        self.path = directory / ".store.lock"
        self.fd: int | None = None

    def __enter__(self):
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self.fd = os.open(self.path, flags, 0o600)
        os.fchmod(self.fd, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        assert self.fd is not None
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _write_temp(directory: Path, identifier: str, payload: bytes) -> str:
    fd, temporary = tempfile.mkstemp(prefix=f".{identifier}.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        # fdopen owns the descriptor once entered; close only if still open.
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _path(store_dir: str | os.PathLike[str], draft_id: str) -> Path:
    if not isinstance(draft_id, str) or not _ID.fullmatch(draft_id):
        raise ValueError("draft id must be 32 lowercase hexadecimal characters")
    return Path(store_dir) / f"{draft_id}.json"


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    return value


def _normalize(draft: dict[str, Any], draft_id: str, created_at: str | None) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise ValueError("draft must be a dictionary")
    result = dict(draft)
    if result.get("id", draft_id) != draft_id:
        raise ValueError("draft id does not match requested id")
    result["id"] = draft_id
    result["created_at"] = _timestamp(created_at or result.get("created_at") or _now(), "created_at")
    result["updated_at"] = _now()
    result.setdefault("source_ids", [])
    result.setdefault("provenance", None)
    result.setdefault("review_state", "needs_review")
    result.setdefault("template_version", None)
    result.setdefault("model", None)
    result.setdefault("metadata", {})
    if not isinstance(result.get("title"), (str, type(None))):
        raise ValueError("title must be a string or None")
    if not isinstance(result.get("content"), str):
        raise ValueError("content must be a string")
    if not isinstance(result["source_ids"], list) or not all(isinstance(item, str) for item in result["source_ids"]):
        raise ValueError("source_ids must be a list of strings")
    if not isinstance(result["review_state"], str) or not result["review_state"].strip():
        raise ValueError("review_state must be a non-empty string")
    for field in ("template_version", "model"):
        if result[field] is not None and not isinstance(result[field], str):
            raise ValueError(f"{field} must be a string or None")
    if not isinstance(result["metadata"], dict):
        raise ValueError("metadata must be a dictionary")
    try:
        encoded = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("draft must contain only JSON-serializable values") from exc
    if len(encoded) > _MAX_RECORD_BYTES:
        raise ValueError(f"draft exceeds {_MAX_RECORD_BYTES} byte limit")
    return result


def save_draft(
    draft: dict[str, Any],
    store_dir: str | os.PathLike[str],
    *,
    draft_id: str | None = None,
    overwrite: bool = False,
    expected_updated_at: str | None = None,
) -> dict[str, Any]:
    """Save a draft under a caller-selected local directory.

    A new ID is generated when absent. Existing files are never replaced unless
    ``overwrite=True`` and the caller supplies the revision it edited. Updates
    are serialized across threads/processes and atomically replaced.
    """
    if not isinstance(draft, dict):
        raise ValueError("draft must be a dictionary")
    supplied_id = draft.get("id")
    if draft_id is not None and supplied_id is not None and draft_id != supplied_id:
        raise ValueError("draft_id does not match draft id")
    identifier = draft_id or supplied_id or uuid.uuid4().hex
    target = _path(store_dir, identifier)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    with _StoreLock(target.parent):
        created_at = None
        if target.exists() or target.is_symlink():
            if not overwrite:
                raise FileExistsError(f"draft {identifier} already exists")
            current = load_draft(identifier, target.parent)
            if expected_updated_at != current["updated_at"]:
                raise DraftConflictError(f"draft {identifier} changed since it was opened; reopen it before saving")
            created_at = current["created_at"]
        elif expected_updated_at is not None:
            raise DraftConflictError(f"draft {identifier} no longer exists; reopen it before saving")
        elif overwrite and supplied_id:
            created_at = draft.get("created_at")

        record = _normalize(draft, identifier, created_at)
        payload = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        temporary = _write_temp(target.parent, identifier, payload)
        try:
            if target.exists() or target.is_symlink():
                # The lock prevents another cooperating writer from intervening.
                os.replace(temporary, target)
            else:
                # Linking is atomic and fails if the destination appeared; unlike
                # writing the destination directly, readers never see partial JSON.
                os.link(temporary, target, follow_symlinks=False)
            _fsync_directory(target.parent)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return record


def load_draft(draft_id: str, store_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate one stored draft; malformed records raise ValueError."""
    target = _path(store_dir, draft_id)
    try:
        if target.is_symlink():
            raise ValueError(f"draft {draft_id} is not a regular file")
        if target.stat().st_size > _MAX_RECORD_BYTES:
            raise ValueError(f"draft {draft_id} exceeds {_MAX_RECORD_BYTES} byte limit")
        with target.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ValueError(f"draft {draft_id} contains invalid JSON") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"draft {draft_id} is not valid UTF-8") from exc
    if not isinstance(record, dict) or record.get("id") != draft_id:
        raise ValueError(f"draft {draft_id} has an invalid record")
    try:
        updated_at = _timestamp(record.get("updated_at"), "updated_at")
        validated = _normalize(record, draft_id, record.get("created_at"))
    except ValueError as exc:
        raise ValueError(f"draft {draft_id} has an invalid record: {exc}") from exc
    validated["updated_at"] = updated_at
    return validated


def list_drafts(store_dir: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Return drafts newest first; reject corrupt records instead of hiding them."""
    directory = Path(store_dir)
    if not directory.exists():
        return []
    records = [load_draft(path.stem, directory) for path in directory.glob("*.json")]
    return sorted(records, key=lambda record: record["updated_at"], reverse=True)


def export_markdown(draft: dict[str, Any]) -> str:
    """Render a readable Markdown brief with provenance and review metadata."""
    if not isinstance(draft, dict):
        raise ValueError("draft must be a dictionary")
    title = draft.get("title") or "Social content draft"
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    sources = draft.get("source_ids", [])
    if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
        raise ValueError("source_ids must be a list of strings")
    content = draft.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    provenance = draft.get("provenance")
    provenance_text = json.dumps(provenance, ensure_ascii=False, indent=2) if isinstance(provenance, (dict, list)) else str(provenance or "Not recorded")
    model = draft.get("model") or "Not used"
    template = draft.get("template_version") or "Not recorded"
    def fence(value: str, language: str = "") -> list[str]:
        longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
        delimiter = "`" * max(3, longest + 1)
        return [f"{delimiter}{language}", value, delimiter]

    safe_title = html.escape(" ".join(title.splitlines()), quote=True).replace("#", r"\#")
    source_text = "\n".join(f"- {source}" for source in sources) if sources else "- None recorded"
    lines = [f"# {safe_title}", "", "## Edited content", "", *fence(content), "", "## Sources", ""]
    lines.extend(fence(source_text))
    lines.extend(["", "## Provenance", "", *fence(provenance_text, "json"), "", "## Review and generation", ""])
    for label, value in (
        ("Review state", draft.get("review_state", "needs_review")),
        ("Created", draft.get("created_at", "Not recorded")),
        ("Updated", draft.get("updated_at", "Not recorded")),
        ("Template version", template), ("Model", model),
    ):
        single_line = " ".join(str(value).splitlines())
        lines.append(f"- {label}: {html.escape(single_line, quote=True)}")
    metadata = draft.get("metadata")
    if metadata:
        try:
            metadata_text = json.dumps(metadata, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain only JSON-serializable values") from exc
        lines.extend(["", "## Additional metadata", "", *fence(metadata_text, "json")])
    lines.append("")
    return "\n".join(lines)
