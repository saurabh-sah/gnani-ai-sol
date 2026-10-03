export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const GITHUB_URL = process.env.NEXT_PUBLIC_GITHUB_URL || "https://github.com/saurabh-sah/gnani-ai-sol";

export type Status = "awaiting_upload" | "queued" | "processing" | "completed" | "failed";

export interface NoteSummary {
  id: string;
  filename: string;
  status: Status;
  stage: string;
  progress: number;
  duration_seconds: number | null;
  created_at: string;
}

export interface Chunk {
  index: number;
  start_seconds: number;
  end_seconds: number;
  text: string | null;
  error: string | null;
}

export interface Note extends NoteSummary {
  language: string;
  size_bytes: number;
  error: string | null;
  transcript: string | null;
  summary: string | null;
  chunks: Chunk[];
}

/** fetch wrapper that turns network failures and FastAPI error bodies into readable messages */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new Error("Can't reach the server. Check your connection; the backend may also be waking up (free hosting), so try again in a few seconds.");
  }
  if (!res.ok) {
    let detail = `Server error (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  languages: () => request<Record<string, string>>("/api/languages"),
  list: () => request<NoteSummary[]>("/api/notes"),
  get: (id: string) => request<Note>(`/api/notes/${id}`),
  create: (body: { filename: string; content_type: string; size_bytes: number; language: string }) =>
    request<{ id: string; upload_url: string }>("/api/notes", { method: "POST", body: JSON.stringify(body) }),
  uploaded: (id: string) => request<NoteSummary>(`/api/notes/${id}/uploaded`, { method: "POST" }),
  uploadFailed: (id: string, reason: string) =>
    request<NoteSummary>(`/api/notes/${id}/upload-failed`, { method: "POST", body: JSON.stringify({ reason }) }),
  retry: (id: string) => request<NoteSummary>(`/api/notes/${id}/retry`, { method: "POST" }),
};

/**
 * Uploads straight to the storage bucket using the signed URL.
 * XMLHttpRequest is used instead of fetch because fetch has no upload progress events.
 */
export function uploadFile(url: string, file: File, onProgress: (pct: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.setRequestHeader("x-upsert", "true");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Storage rejected the upload (${xhr.status}).`));
    xhr.onerror = () => reject(new Error("Network error while uploading. Check your connection and try again."));
    xhr.ontimeout = () => reject(new Error("Upload timed out."));
    xhr.send(file);
  });
}

export function formatDuration(sec: number | null): string {
  if (sec == null) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}
