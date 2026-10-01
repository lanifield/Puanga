from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_DATA_FILE = r"G:\Mahara\SYSTEM\Apps\Puanga\time-entries.json"
DATA_FILE = Path(os.environ.get("PUANGA_DATA_FILE", DEFAULT_DATA_FILE))

# Single-worker process assumed: an in-process lock is enough to serialize
# read-modify-write cycles. If this service is ever run with multiple
# workers, this needs to become a cross-process file lock instead.
_lock = asyncio.Lock()


def _read_raw() -> dict[str, Any]:
    if not DATA_FILE.exists():
        return {"entries": []}
    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_raw(data: dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_FILE.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, DATA_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


async def read_entries() -> list[dict[str, Any]]:
    async with _lock:
        return _read_raw()["entries"]


async def get_running_entry() -> dict[str, Any] | None:
    async with _lock:
        entries = _read_raw()["entries"]
        return next((e for e in entries if e["end_time"] is None), None)


async def append_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Returns None (and does not write) if a running entry already exists."""
    async with _lock:
        data = _read_raw()
        if any(e["end_time"] is None for e in data["entries"]):
            return None
        data["entries"].append(entry)
        _write_raw(data)
        return entry


async def update_entry(entry_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    async with _lock:
        data = _read_raw()
        for e in data["entries"]:
            if e["id"] == entry_id:
                e.update(updates)
                _write_raw(data)
                return e
        return None


async def upsert_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert or replace by id, so a retried /entries/sync batch never duplicates."""
    async with _lock:
        data = _read_raw()
        by_id = {e["id"]: i for i, e in enumerate(data["entries"])}
        result = []
        for entry in entries:
            if entry["id"] in by_id:
                data["entries"][by_id[entry["id"]]] = entry
            else:
                data["entries"].append(entry)
                by_id[entry["id"]] = len(data["entries"]) - 1
            result.append(entry)
        _write_raw(data)
        return result
