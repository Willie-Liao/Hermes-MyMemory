"""Recovery-safe execution records for memory-digest operation batches."""

from __future__ import annotations

import hashlib
import copy
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

STATES = (
    "prepared",
    "validated",
    "executing",
    "candidate_validated",
    "committed",
    "failed",
    "uncertain",
)
_TERMINAL_STATES = {"committed", "failed", "uncertain"}
_RECOVERABLE_STATES = {"prepared", "validated", "executing", "candidate_validated"}
_NEXT_STATES = {
    "prepared": {"validated", "failed"},
    "validated": {"executing", "failed"},
    "executing": {"candidate_validated", "failed"},
    "candidate_validated": {"committed", "failed", "uncertain"},
    "committed": set(),
    "failed": set(),
    "uncertain": set(),
}


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def file_version(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False, "size": 0, "mtime_ns": None}
    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def _atomic_replace_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _json_safe_operations(operations: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for operation in operations:
        if hasattr(operation, "to_dict"):
            operation = operation.to_dict()
        result.append(dict(operation) if isinstance(operation, Mapping) else {"value": operation})
    return result


class ExecutionLog:
    """Small append-by-replacement log; the canonical daily file remains authoritative."""

    def __init__(self, path: Path, payload: dict[str, Any]) -> None:
        self.path = path
        self.payload = payload

    @property
    def state(self) -> str:
        return str(self.payload["state"])

    @classmethod
    def create(
        cls,
        session_dir: Path,
        *,
        session_id: str,
        run_id: str,
        base_content: str,
        base_version: Mapping[str, Any] | None = None,
        operations: Iterable[Any],
    ) -> "ExecutionLog":
        path = Path(session_dir) / "execution.json"
        now = _timestamp()
        payload = {
            "session_id": session_id,
            "run_id": run_id,
            "state": "prepared",
            "base_file_hash": content_hash(base_content),
            "base_file_version": dict(base_version or {}),
            "operations": _json_safe_operations(operations),
            "created_at": now,
            "updated_at": now,
            "state_timestamps": {"prepared": now},
            "failure": None,
        }
        log = cls(path, payload)
        log._write()
        return log

    def set_operations(self, operations: Iterable[Any]) -> None:
        if self.state != "prepared":
            raise ValueError("operation list can only be set while prepared")
        self.payload["operations"] = _json_safe_operations(operations)
        self._write()

    def transition(
        self,
        state: str,
        *,
        failure: Mapping[str, Any] | None = None,
    ) -> None:
        if state not in STATES:
            raise ValueError(f"unknown execution state: {state}")
        if state not in _NEXT_STATES[self.state]:
            raise ValueError(f"invalid execution transition: {self.state} -> {state}")
        previous = copy.deepcopy(self.payload)
        now = _timestamp()
        self.payload["state"] = state
        self.payload["updated_at"] = now
        self.payload.setdefault("state_timestamps", {})[state] = now
        if failure is not None:
            self.payload["failure"] = dict(failure)
        try:
            self._write()
        except BaseException:
            self.payload = previous
            raise

    def fail(self, error: BaseException | str, *, prior_state: str | None = None) -> None:
        if self.state in _TERMINAL_STATES:
            return
        message = str(error)
        self.transition(
            "failed",
            failure={
                "kind": "execution_error",
                "message": message,
                "state": prior_state or self.state,
            },
        )

    def mark_uncertain(self, error: BaseException | str) -> None:
        """Record that replacement succeeded but the committed log did not."""
        now = _timestamp()
        self.payload["state"] = "uncertain"
        self.payload["updated_at"] = now
        self.payload.setdefault("state_timestamps", {})["uncertain"] = now
        self.payload["failure"] = {
            "kind": "recovery_required",
            "message": str(error),
            "state": "replacement_succeeded_commit_log_failed",
        }
        self._write()

    def _write(self) -> None:
        _atomic_replace_text(
            self.path,
            json.dumps(self.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def recover_interrupted(path: Path) -> dict[str, Any]:
    """Mark non-terminal records failed; never infer that an interrupted write committed."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    state = payload.get("state")
    if state in _RECOVERABLE_STATES:
        now = _timestamp()
        payload["state"] = "failed"
        payload["updated_at"] = now
        payload.setdefault("state_timestamps", {})["failed"] = now
        payload["failure"] = {
            "kind": "interrupted",
            "message": "execution interrupted before atomic replacement completed",
            "prior_state": state,
        }
        _atomic_replace_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    return payload


def cleanup_terminal_artifacts(
    execution_path: Path,
    *,
    session_id: str,
    run_id: str,
) -> list[Path]:
    """Delete only this run's validated inputs after a terminal execution state."""
    execution_path = Path(execution_path).resolve()
    payload = json.loads(execution_path.read_text(encoding="utf-8"))
    if payload.get("state") not in _TERMINAL_STATES:
        raise ValueError("cannot clean up artifacts before a terminal execution state")
    if payload.get("session_id") != session_id or payload.get("run_id") != run_id:
        raise ValueError("execution record identity does not match cleanup request")

    session_dir = execution_path.parent
    candidates = [
        session_dir / name
        for name in (
            "event-result.json",
            "fact-result.json",
            "procedure-result.json",
            "decision-result.json",
            "event-failures.jsonl",
            "fact-failures.jsonl",
            "procedure-failures.jsonl",
            "decision-failures.jsonl",
            "worker-manifest.json",
            "operations.json",
        )
    ]
    removed: list[Path] = []
    for candidate in candidates:
        if candidate == execution_path or not candidate.is_file():
            continue
        if candidate.suffix == ".jsonl":
            # Failure ledgers carry session/run on every line; after execution
            # identity is confirmed above, delete by fixed filename.
            candidate.unlink()
            removed.append(candidate)
            continue
        try:
            artifact = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if artifact.get("session_id") != session_id or artifact.get("run_id") != run_id:
            continue
        candidate.unlink()
        removed.append(candidate)
    if removed:
        _fsync_directory(session_dir)
    return removed
