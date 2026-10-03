"""HTTP API. Everything here is fast; slow work (ASR, LLM) happens in worker.py."""
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import config, storage, worker
from .db import Chunk, Job, Note, SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".mp4", ".opus", ".wma", ".amr"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = threading.Event()
    if config.RUN_WORKER_IN_API:
        threading.Thread(target=worker.loop, args=(stop,), daemon=True, name="worker").start()
    yield
    stop.set()


app = FastAPI(title="Audio Notes API", lifespan=lifespan)


# Registered before CORS so it sits *inside* the CORS layer: an unexpected crash still
# returns JSON with CORS headers, and the browser shows the real error instead of
# a generic "can't reach the server".
@app.middleware("http")
async def catch_unhandled(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("api").exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"Internal error: {exc.__class__.__name__}: {exc}"})


app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(storage.StorageError)
async def storage_error_handler(request: Request, exc: storage.StorageError):
    return JSONResponse(status_code=502, content={"detail": f"Storage problem: {exc}"})


# ---------------------------------------------------------------- schemas

class CreateNoteIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0)
    language: str = "en-IN"


class CreateNoteOut(BaseModel):
    id: str
    upload_url: str


class UploadFailedIn(BaseModel):
    reason: str = "Upload failed"


class NoteSummaryOut(BaseModel):
    id: str
    filename: str
    status: str
    stage: str
    progress: int
    duration_seconds: float | None
    created_at: datetime


class ChunkOut(BaseModel):
    index: int
    start_seconds: float
    end_seconds: float
    text: str | None
    error: str | None


class NoteOut(NoteSummaryOut):
    language: str
    size_bytes: int
    error: str | None
    transcript: str | None
    summary: str | None
    chunks: list[ChunkOut]


def _summary(n: Note) -> dict:
    return dict(id=n.id, filename=n.filename, status=n.status, stage=n.stage, progress=n.progress,
                duration_seconds=n.duration_seconds, created_at=n.created_at)


def _get_or_404(s, note_id: str) -> Note:
    note = s.get(Note, note_id)
    if not note:
        raise HTTPException(404, "Note not found")
    return note


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/languages")
def languages():
    return config.SUPPORTED_LANGUAGES


@app.post("/api/notes", response_model=CreateNoteOut)
def create_note(body: CreateNoteIn):
    """Step 1 of upload: register the note and hand back a signed URL for the browser to upload to."""
    ext = ("." + body.filename.rsplit(".", 1)[-1].lower()) if "." in body.filename else ""
    if not body.content_type.startswith(("audio/", "video/")) and ext not in AUDIO_EXTENSIONS:
        raise HTTPException(400, "That doesn't look like an audio file.")
    if body.size_bytes > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    if body.language not in config.SUPPORTED_LANGUAGES:
        raise HTTPException(400, "Unsupported language.")

    note_id = str(uuid.uuid4())
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", body.filename)[-100:]
    path = f"{note_id}/{safe_name}"
    try:
        upload_url = storage.create_signed_upload_url(path)
    except storage.StorageError as exc:
        raise HTTPException(502, f"Storage is unavailable: {exc}")

    with SessionLocal() as s, s.begin():
        s.add(Note(id=note_id, filename=body.filename, content_type=body.content_type,
                   size_bytes=body.size_bytes, language=body.language, storage_path=path))
    return CreateNoteOut(id=note_id, upload_url=upload_url)


@app.post("/api/notes/{note_id}/uploaded", response_model=NoteSummaryOut)
def mark_uploaded(note_id: str):
    """Step 2 of upload: the browser says it finished; we confirm the object exists, then enqueue."""
    with SessionLocal() as s, s.begin():
        note = _get_or_404(s, note_id)
        if note.status != "awaiting_upload":
            return _summary(note)
        if not storage.object_exists(note.storage_path):
            note.status, note.stage, note.error = "failed", "Failed", "The file never reached storage. Please upload again."
            return _summary(note)
        note.status, note.stage, note.progress = "queued", "Waiting for a worker", 2
        worker.enqueue(s, note_id)
        return _summary(note)


@app.post("/api/notes/{note_id}/upload-failed", response_model=NoteSummaryOut)
def mark_upload_failed(note_id: str, body: UploadFailedIn):
    with SessionLocal() as s, s.begin():
        note = _get_or_404(s, note_id)
        if note.status == "awaiting_upload":
            note.status, note.stage, note.error = "failed", "Upload failed", body.reason[:500]
        return _summary(note)


@app.get("/api/notes", response_model=list[NoteSummaryOut])
def list_notes(limit: int = 50):
    with SessionLocal() as s:
        notes = s.scalars(select(Note).where(Note.status != "awaiting_upload")
                          .order_by(Note.created_at.desc()).limit(min(limit, 200))).all()
        return [_summary(n) for n in notes]


@app.get("/api/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: str):
    with SessionLocal() as s:
        n = _get_or_404(s, note_id)
        return NoteOut(**_summary(n), language=n.language, size_bytes=n.size_bytes, error=n.error,
                       transcript=n.transcript, summary=n.summary,
                       chunks=[ChunkOut(index=c.index, start_seconds=c.start_seconds, end_seconds=c.end_seconds,
                                        text=c.text, error=c.error) for c in n.chunks])


@app.post("/api/notes/{note_id}/retry", response_model=NoteSummaryOut)
def retry(note_id: str):
    """Manual retry from the UI after a permanent failure. Already transcribed pieces are kept."""
    with SessionLocal() as s, s.begin():
        note = _get_or_404(s, note_id)
        if note.status != "failed":
            raise HTTPException(409, "Only failed notes can be retried.")
        if not storage.object_exists(note.storage_path):
            raise HTTPException(409, "The original file is missing from storage. Please upload it again.")
        pending = s.scalar(select(Job).where(Job.note_id == note_id, Job.status.in_(["pending", "running"])))
        if not pending:
            worker.enqueue(s, note_id)
        note.status, note.stage, note.progress, note.error = "queued", "Waiting for a worker", 2, None
        return _summary(note)
