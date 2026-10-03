"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, formatDuration, Note } from "@/lib/api";

const POLL_MS = 2000;

export default function NotePage({ params }: { params: { id: string } }) {
  const [note, setNote] = useState<Note | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<"summary" | "transcript">("summary");
  const [retrying, setRetrying] = useState(false);
  const [tick, setTick] = useState(0);

  // Poll until the job reaches a final state. Network blips are shown but polling keeps going.
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const n = await api.get(params.id);
        if (stopped) return;
        setNote(n);
        setLoadError(null);
        if (n.status === "completed" || n.status === "failed") return;
      } catch (e) {
        if (stopped) return;
        setLoadError((e as Error).message);
        if ((e as Error).message === "Note not found") return;
      }
      timer = setTimeout(load, POLL_MS);
    }
    load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [params.id, tick]);

  async function retry() {
    setRetrying(true);
    try {
      await api.retry(params.id);
      setTick((t) => t + 1); // restart polling
    } catch (e) {
      setLoadError((e as Error).message);
    } finally {
      setRetrying(false);
    }
  }

  if (!note) {
    return (
      <>
        <Link href="/">← All uploads</Link>
        {loadError ? <div className="alert">{loadError}</div> : <p className="muted">Loading…</p>}
      </>
    );
  }

  const inFlight = note.status === "queued" || note.status === "processing";

  return (
    <>
      <Link href="/">← All uploads</Link>
      <h1 style={{ marginTop: 12 }}>{note.filename}</h1>
      <p className="muted">
        {new Date(note.created_at).toLocaleString()} · {formatDuration(note.duration_seconds)} ·{" "}
        {(note.size_bytes / 1024 / 1024).toFixed(1)} MB · {note.language}
      </p>

      {loadError && <div className="alert">Connection problem: {loadError} (still retrying)</div>}

      {note.status !== "completed" && (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <strong>{note.stage}</strong>
            <span className={`badge ${note.status}`}>{note.status}</span>
          </div>
          <div className={`bar ${note.status === "failed" ? "failed" : ""}`}>
            <div style={{ width: `${Math.max(note.progress, 2)}%` }} />
          </div>
          {inFlight && (
            <p className="muted">
              {note.progress}% · Long recordings are split into short pieces and transcribed in parallel.
              You can leave this page and come back later.
            </p>
          )}
          {note.error && (
            <div className="alert" role="alert">
              {note.status === "failed" ? "Processing failed: " : "Last attempt failed, retrying automatically: "}
              {note.error}
            </div>
          )}
          {note.status === "failed" && (
            <div className="row">
              <button className="primary" onClick={retry} disabled={retrying}>
                {retrying ? "Retrying…" : "Retry"}
              </button>
              <Link href="/">Upload a different file</Link>
            </div>
          )}
        </section>
      )}

      {(note.summary || note.chunks.some((c) => c.text)) && (
        <section className="card">
          <div className="tabs row" style={{ marginTop: 0, marginBottom: 14 }}>
            <button className={tab === "summary" ? "on" : ""} onClick={() => setTab("summary")}>Summary</button>
            <button className={tab === "transcript" ? "on" : ""} onClick={() => setTab("transcript")}>Transcript</button>
          </div>

          {tab === "summary" && (
            note.summary
              ? <div className="prose"><ReactMarkdown>{note.summary}</ReactMarkdown></div>
              : <p className="muted">The summary will appear once the transcript is finished.</p>
          )}

          {tab === "transcript" && (
            <div>
              {note.chunks.map((c) => (
                <div className="seg" key={c.index}>
                  <time>{formatDuration(c.start_seconds)}</time>
                  <span>
                    {c.text !== null ? (c.text || <em className="muted">(no speech)</em>)
                      : c.error ? <em style={{ color: "var(--danger)" }}>Failed: {c.error}</em>
                      : <em className="muted">Transcribing…</em>}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </>
  );
}
