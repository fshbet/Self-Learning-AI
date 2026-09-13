"""Canonical Knowledge Snapshot API: build, list, inspect, download, verify."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.export.delta import build_delta_snapshot
from ..core.export.snapshot import build_snapshot, read_file, verify_snapshot, zip_snapshot
from ..core.orchestration.jobs import start_run
from ..core.orchestration.queue import enqueue
from ..core.plugins.registry import get_registry
from ..db import get_db
from ..models import Snapshot
from .schemas import ORM

router = APIRouter(tags=["snapshots"])


class SnapshotOut(ORM):
    id: uuid.UUID
    domain_id: str
    version: int
    kind: str
    base_snapshot_id: uuid.UUID | None
    manifest: dict[str, Any]
    integrity_hash: str | None
    object_prefix: str | None
    size_bytes: int
    status: str
    error: str | None
    created_by: str
    created_at: Any
    finished_at: Any


class SnapshotCreate(BaseModel):
    domain: str
    kind: str = Field(default="full", pattern="^(full|delta)$")
    base_snapshot_id: uuid.UUID | None = None
    wait: bool = False


@router.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots(
    domain: str | None = None, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)
) -> list[SnapshotOut]:
    stmt = select(Snapshot).order_by(Snapshot.created_at.desc()).limit(limit)
    if domain:
        stmt = stmt.where(Snapshot.domain_id == domain)
    return [SnapshotOut.model_validate(s) for s in db.execute(stmt).scalars().all()]


@router.post("/snapshots", response_model=SnapshotOut, status_code=201)
def create_snapshot(body: SnapshotCreate, db: Session = Depends(get_db)) -> SnapshotOut:
    reg = get_registry()
    if body.domain not in reg:
        raise HTTPException(404, f"unknown domain {body.domain}")
    if body.kind == "delta":
        try:
            snap = build_delta_snapshot(
                db, reg.get(body.domain), base_snapshot_id=body.base_snapshot_id, created_by="api"
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from exc
        db.commit()
        return SnapshotOut.model_validate(snap)
    if body.wait:
        snap = build_snapshot(db, reg.get(body.domain), created_by="api")
        db.commit()
        return SnapshotOut.model_validate(snap)
    run = start_run(db, domain_id=body.domain, kind="snapshot", triggered_by="api")
    job = enqueue(
        db,
        "snapshot",
        {"domain_id": body.domain, "created_by": "api"},
        run_id=run.id,
        idempotency_key=f"snapshot:{body.domain}",
        priority=110,
        max_attempts=1,
    )
    db.commit()
    if job is None:
        raise HTTPException(409, "a snapshot for this domain is already being built")
    placeholder = Snapshot(id=run.id, domain_id=body.domain, version=0, kind="full", status="queued", created_by="api")
    return SnapshotOut.model_validate(placeholder)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(snapshot_id: uuid.UUID, db: Session = Depends(get_db)) -> SnapshotOut:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(404, "snapshot not found")
    return SnapshotOut.model_validate(snap)


@router.get("/snapshots/{snapshot_id}/files/{path:path}")
def get_snapshot_file(snapshot_id: uuid.UUID, path: str, db: Session = Depends(get_db)) -> Response:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None or snap.status != "ready":
        raise HTTPException(404, "snapshot not found or not ready")
    files = (snap.manifest or {}).get("files") or {}
    if path != "manifest.json" and path not in files:
        raise HTTPException(404, "file not in snapshot")
    data = read_file(snap, path)
    media = (
        "application/json"
        if path.endswith(".json")
        else "application/x-ndjson"
        if path.endswith(".jsonl")
        else "text/html; charset=utf-8"
        if path.endswith(".html")
        else "text/markdown; charset=utf-8"
        if path.endswith(".md")
        else "application/octet-stream"
    )
    return Response(content=data, media_type=media)


@router.get("/snapshots/{snapshot_id}/download")
def download_snapshot(snapshot_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None or snap.status != "ready":
        raise HTTPException(404, "snapshot not found or not ready")
    data = zip_snapshot(snap)
    name = f"{snap.domain_id}-knowledge-v{snap.version}.zip"
    return Response(
        content=data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.post("/snapshots/{snapshot_id}/verify")
def verify(snapshot_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None or snap.status != "ready":
        raise HTTPException(404, "snapshot not found or not ready")
    return verify_snapshot(snap)
