from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import storage
import vault
from models import Entry, OptionsResponse, StartEntryRequest, StopEntryRequest, SyncRequest

app = FastAPI(title="Puanga local service")

# Loosened for local dev against a phone on the home network. Tighten to the
# PWA's actual origin once it's served from a fixed address.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/options", response_model=OptionsResponse)
async def get_options():
    return {"options": vault.scan_options()}


@app.post("/entries/start", response_model=Entry)
async def start_entry(body: StartEntryRequest):
    matching = next((o for o in vault.scan_options() if o["value"] == body.project_id), None)
    if matching is None:
        raise HTTPException(
            status_code=422,
            detail=f"'{body.project_id}' isn't a known project or course — check GET /options",
        )

    entry = {
        "id": str(uuid.uuid4()),
        "client_id": matching["client"],
        "project_id": body.project_id,
        "task": body.task,
        "notes": body.notes,
        "start_time": _now().isoformat(),
        "end_time": None,
        "duration_minutes": None,
        "synced_at": _now().isoformat(),
        "created_offline": False,
    }
    result = await storage.append_entry(entry)
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="An entry is already running — stop it before starting a new one",
        )
    return result


@app.post("/entries/{entry_id}/stop", response_model=Entry)
async def stop_entry(entry_id: str, body: StopEntryRequest):
    entries = await storage.read_entries()
    existing = next((e for e in entries if e["id"] == entry_id), None)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"No entry with id {entry_id}")
    if existing["end_time"] is not None:
        raise HTTPException(status_code=409, detail="Entry is already stopped")

    end_time = body.end_time or _now()
    start_time = datetime.fromisoformat(existing["start_time"])
    duration_minutes = round((end_time - start_time).total_seconds() / 60, 2)

    updated = await storage.update_entry(
        entry_id,
        {
            "end_time": end_time.isoformat(),
            "duration_minutes": duration_minutes,
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No entry with id {entry_id}")
    return updated


@app.get("/entries", response_model=list[Entry])
async def list_entries(
    project_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
):
    entries = await storage.read_entries()
    if project_id:
        entries = [e for e in entries if e["project_id"] == project_id]
    if since:
        entries = [e for e in entries if datetime.fromisoformat(e["start_time"]) >= since]
    if until:
        entries = [e for e in entries if datetime.fromisoformat(e["start_time"]) <= until]
    return entries


@app.post("/entries/sync", response_model=list[Entry])
async def sync_entries(body: SyncRequest):
    now = _now().isoformat()
    payload = []
    for e in body.entries:
        d = e.model_dump()
        d["id"] = d["id"] or str(uuid.uuid4())
        d["start_time"] = e.start_time.isoformat()
        d["end_time"] = e.end_time.isoformat() if e.end_time else None
        d["synced_at"] = now
        payload.append(d)
    return await storage.upsert_entries(payload)
