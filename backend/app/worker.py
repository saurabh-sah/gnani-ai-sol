"""Background worker: a Postgres-backed job queue.

Why Postgres instead of Redis/Celery: we already need Postgres, and
`SELECT ... FOR UPDATE SKIP LOCKED` lets several workers pull from the same
table without ever claiming the same job twice. One less service to deploy.

Run standalone with:  python -m app.worker
"""
import logging
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, text

from . import asr, audio, config, storage, summarizer
from .db import Chunk, Job, Note, SessionLocal, utcnow

log = logging.getLogger("worker")

POLL_SECONDS = 2
STALE_AFTER = timedelta(minutes=15)  # a 'running' job older than this is assumed to belong to a crashed worker


class PermanentError(Exception):
    """Errors where retrying cannot help (corrupt file, bad API key)."""


# ---------------------------------------------------------------- queue helpers

def enqueue(session, note_id: str) -> None:
    session.add(Job(note_id=note_id))


def claim_next_job() -> tuple[int, str] | None:
    with SessionLocal() as s, s.begin():
        row = s.execute(text("""
            SELECT id, note_id FROM jobs
            WHERE status = 'pending' AND run_after <= now()
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """)).first()
        if not row:
            return None
        s.execute(text("UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 WHERE id=:id"),
                  {"id": row.id})
        return row.id, row.note_id


def requeue_stale_jobs() -> None:
    """If a worker died mid-job (deploy, crash, OOM), put its job back in the queue."""
    with SessionLocal() as s, s.begin():
        s.execute(text("UPDATE jobs SET status='pending', locked_at=NULL "
                       "WHERE status='running' AND locked_at < :cutoff"),
                  {"cutoff": utcnow() - STALE_AFTER})


def set_note(note_id: str, **fields) -> None:
    with SessionLocal() as s, s.begin():
        note = s.get(Note, note_id)
        if note:
            for k, v in fields.items():
                setattr(note, k, v)


# ---------------------------------------------------------------- the pipeline

def process_note(note_id: str) -> None:
    with SessionLocal() as s:
        note = s.get(Note, note_id)
        if note is None:
            raise PermanentError("Note was deleted")
        storage_path, language = note.storage_path, note.language

    workdir = Path(tempfile.mkdtemp(prefix="note_"))
    try:
        # 1. Download from the bucket
        set_note(note_id, status="processing", stage="Downloading audio", progress=3, error=None)
        src = workdir / "source"
        storage.download_to(storage_path, src)

        # 2. Validate and measure
        set_note(note_id, stage="Checking audio file", progress=6)
        try:
            duration = audio.probe_duration(src)
        except audio.CorruptAudioError as exc:
            raise PermanentError(str(exc))
        set_note(note_id, duration_seconds=duration)

        # 3. Cut into fixed-length pieces
        set_note(note_id, stage="Splitting audio into pieces", progress=9)
        try:
            pieces = audio.split_fixed(src, workdir / "pieces", config.CHUNK_SECONDS, duration)
        except audio.CorruptAudioError as exc:
            raise PermanentError(str(exc))

        # Chunk rows survive between attempts so a retry only redoes the missing pieces.
        with SessionLocal() as s, s.begin():
            existing = {c.index: c for c in s.scalars(select(Chunk).where(Chunk.note_id == note_id))}
            if len(existing) != len(pieces):  # first run (or chunk size changed): start fresh
                for c in existing.values():
                    s.delete(c)
                s.flush()
                for p in pieces:
                    s.add(Chunk(note_id=note_id, index=p.index, start_seconds=p.start, end_seconds=p.end))
                done_indexes = set()
            else:
                done_indexes = {i for i, c in existing.items() if c.text is not None}

        # 4. Transcribe pieces in parallel (Gnani limit is 60s per request)
        todo = [p for p in pieces if p.index not in done_indexes]
        total = len(pieces)
        finished = len(done_indexes)
        lock = threading.Lock()
        failures: list[str] = []

        def report():
            pct = 10 + int(75 * finished / total)
            set_note(note_id, stage=f"Transcribing: {finished} of {total} pieces done", progress=pct)

        report()

        def work(piece: audio.Piece) -> tuple[audio.Piece, str | None, str | None]:
            try:
                return piece, asr.transcribe_piece(piece.path, language), None
            except asr.ASRError as exc:
                return piece, None, str(exc)

        with ThreadPoolExecutor(max_workers=config.ASR_PARALLELISM) as pool:
            for fut in as_completed([pool.submit(work, p) for p in todo]):
                piece, result_text, err = fut.result()
                with SessionLocal() as s, s.begin():
                    chunk = s.scalar(select(Chunk).where(Chunk.note_id == note_id, Chunk.index == piece.index))
                    chunk.text, chunk.error = result_text, err
                with lock:
                    if err:
                        failures.append(f"piece {piece.index + 1}: {err}")
                    else:
                        finished += 1
                    report()

        if failures:
            if any("invalid or out of credits" in f for f in failures):
                raise PermanentError(failures[0])
            raise RuntimeError(f"{len(failures)} of {total} pieces failed to transcribe. First error: {failures[0]}")

        # 5. Stitch transcript in order
        with SessionLocal() as s:
            chunks = s.scalars(select(Chunk).where(Chunk.note_id == note_id).order_by(Chunk.index)).all()
            transcript = " ".join(c.text for c in chunks if c.text).strip()
        set_note(note_id, transcript=transcript, stage="Writing summary", progress=90)

        # 6. Summarise
        summary = summarizer.summarise(transcript)
        set_note(note_id, summary=summary, status="completed", stage="Done", progress=100, error=None)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- job runner

def run_one(job_id: int, note_id: str) -> None:
    try:
        process_note(note_id)
        with SessionLocal() as s, s.begin():
            s.get(Job, job_id).status = "done"
        log.info("job %s finished", job_id)
    except Exception as exc:  # noqa: BLE001 - every failure must reach the user
        log.exception("job %s failed", job_id)
        message = str(exc) or exc.__class__.__name__
        with SessionLocal() as s, s.begin():
            job = s.get(Job, job_id)
            job.last_error = message
            retryable = not isinstance(exc, PermanentError) and job.attempts < config.MAX_JOB_ATTEMPTS
            if retryable:
                wait = 30 * job.attempts
                job.status, job.locked_at = "pending", None
                job.run_after = utcnow() + timedelta(seconds=wait)
                note = s.get(Note, note_id)
                if note:
                    note.status = "queued"
                    note.stage = (f"Attempt {job.attempts} of {config.MAX_JOB_ATTEMPTS} failed, "
                                  f"retrying in {wait}s")
                    note.error = message
            else:
                job.status = "dead"
                note = s.get(Note, note_id)
                if note:
                    note.status, note.stage, note.error = "failed", "Failed", message


def loop(stop: threading.Event | None = None) -> None:
    log.info("worker started")
    last_stale_check = 0.0
    while not (stop and stop.is_set()):
        try:
            if time.time() - last_stale_check > 60:
                requeue_stale_jobs()
                last_stale_check = time.time()
            claimed = claim_next_job()
            if claimed:
                run_one(*claimed)
                continue
        except Exception:  # noqa: BLE001 - e.g. DB briefly unreachable; keep the loop alive
            log.exception("worker loop error")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .db import init_db
    init_db()
    loop()
