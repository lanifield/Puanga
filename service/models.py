from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _not_blank(v: str, field_name: str) -> str:
    if not v or not v.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return v


class TaskFields(BaseModel):
    project_id: str
    task: str
    notes: Optional[str] = None

    @field_validator("project_id")
    @classmethod
    def project_id_not_blank(cls, v: str) -> str:
        return _not_blank(v, "project_id")

    @field_validator("task")
    @classmethod
    def task_not_blank(cls, v: str) -> str:
        return _not_blank(v, "task")


class StartEntryRequest(TaskFields):
    """client_id is deliberately absent here — the live /entries/start path
    derives it server-side from the chosen project's vault frontmatter
    (client for a project, provider for a course), rather than trusting
    whatever a front-end sends."""


class StopEntryRequest(BaseModel):
    end_time: Optional[datetime] = None


class ClientTaskFields(TaskFields):
    client_id: str = Field(..., description="The client (project's client, or course's provider) this work is for")

    @field_validator("client_id")
    @classmethod
    def client_id_not_blank(cls, v: str) -> str:
        return _not_blank(v, "client_id")


class SyncEntry(ClientTaskFields):
    """The offline-queue path: the front-end already resolved client_id from
    its own last-known cache of vault options while offline, so it's taken
    as given here rather than re-derived (re-deriving could disagree with
    what was true when the entry was actually created, if the project's
    frontmatter changed in the meantime)."""

    id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_minutes: Optional[float] = None
    created_offline: bool = True


class SyncRequest(BaseModel):
    entries: list[SyncEntry]


class Entry(ClientTaskFields):
    id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_minutes: Optional[float] = None
    synced_at: Optional[datetime] = None
    created_offline: bool = False


class VaultOption(BaseModel):
    value: str
    label: str
    client: str


class OptionsResponse(BaseModel):
    options: list[VaultOption]
