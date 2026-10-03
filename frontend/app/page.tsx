"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatDuration, NoteSummary, uploadFile } from "@/lib/api";

const LANGS: Record<string, string> = {
  "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "gu-IN": "Gujarati", "kn-IN": "Kannada",
  "ml-IN": "Malayalam", "mr-IN": "Marathi", "pa-IN": "Punjabi", "ta-IN": "Tamil", "te-IN": "Telugu",
};

type Phase = "idle" | "preparing" | "uploading" | "finishing";

export default function Home() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("en-IN");
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploadPct, setUploadPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const [notes, setNotes] = useState<NoteSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadNotes = useCallback(async () => {
    try {
      setNotes(await api.list());
      setListError(null);
    } catch (e) {
      setListError((e as Error).message);
    }
  }, []);

  // Refresh the history while anything is still being processed.
  useEffect(() => {
    loadNotes();
    const t = setInterval(loadNotes, 5000);
    return () => clearInterval(t);
  }, [loadNotes]);

  function pick(f: File | undefined) {
    setError(null);
    if (!f) return;
    const looksAudio = f.type.startsWith("audio/") || f.type.startsWith("video/") ||
      /\.(wav|mp3|m4a|aac|ogg|flac|webm|mp4|opus|wma|amr)$/i.test(f.name);
    if (!looksAudio) {
      setError("Please choose an audio file (mp3, wav, m4a, ogg, flac, …).");
      return;
    }
    setFile(f);
  }

  async function start() {
    if (!file) return;
    setError(null);
    let noteId: string | null = null;
    try {
      setPhase("preparing");
      const created = await api.create({
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size_bytes: file.size,
        language,
      });
      noteId = created.id;

      setPhase("uploading");
      setUploadPct(0);
      await uploadFile(created.upload_url, file, setUploadPct);

      setPhase("finishing");
      await api.uploaded(created.id);
      router.push(`/notes/${created.id}`);
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      setPhase("idle");
      if (noteId) api.uploadFailed(noteId, msg).catch(() => {});
    }
  }

  const busy = phase !== "idle";
  const phaseText = {
    idle: "",
    preparing: "Preparing upload…",
    uploading: `Uploading… ${uploadPct}%`,
    finishing: "Upload complete, queueing for transcription…",
  }[phase];

  return (
    <>
      <h1>Audio Notes</h1>
      <p className="muted">Upload a recording of any length. You'll get a transcript (via Gnani ASR) and a summary.</p>

      <section className="card">
        <div
          className={`dropzone ${dragging ? "active" : ""}`}
          onClick={() => !busy && inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); if (!busy) pick(e.dataTransfer.files[0]); }}
        >
          {file ? (
            <>
              <strong>{file.name}</strong>
              <div className="muted">{(file.size / 1024 / 1024).toFixed(1)} MB · click to change</div>
            </>
          ) : (
            <>
              <strong>Drop an audio file here</strong>
              <div className="muted">or click to browse</div>
            </>
          )}
          <input ref={inputRef} type="file" accept="audio/*,video/*" hidden
                 onChange={(e) => pick(e.target.files?.[0])} />
        </div>

        <div className="row">
          <label className="muted" htmlFor="lang">Spoken language</label>
          <select id="lang" value={language} onChange={(e) => setLanguage(e.target.value)} disabled={busy}>
            {Object.entries(LANGS).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
          <button className="primary" onClick={start} disabled={!file || busy}>
            {busy ? "Working…" : "Transcribe"}
          </button>
        </div>

        {busy && (
          <div style={{ marginTop: 14 }}>
            <div className="muted">{phaseText}</div>
            <div className="bar"><div style={{ width: `${phase === "uploading" ? uploadPct : phase === "finishing" ? 100 : 3}%` }} /></div>
          </div>
        )}
        {error && <div className="alert" role="alert">{error}</div>}
      </section>

      <section className="card">
        <h2>Past uploads</h2>
        {listError && <div className="alert">Couldn't load history: {listError}</div>}
        {notes === null && !listError && <p className="muted">Loading…</p>}
        {notes?.length === 0 && <p className="muted">Nothing yet. Your uploads will appear here.</p>}
        {notes && notes.length > 0 && (
          <ul className="list">
            {notes.map((n) => (
              <li key={n.id}>
                <Link href={`/notes/${n.id}`}>
                  <span>
                    <strong>{n.filename}</strong>
                    <div className="muted">
                      {new Date(n.created_at).toLocaleString()} · {formatDuration(n.duration_seconds)}
                      {(n.status === "processing" || n.status === "queued") && ` · ${n.progress}%`}
                    </div>
                  </span>
                  <span className={`badge ${n.status}`}>{n.status}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
