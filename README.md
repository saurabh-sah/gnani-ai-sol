# Audio Notes

Upload an audio file of any length and get a transcript (Gnani ASR) and a summary (Gemini).

- **Frontend:** Next.js 14 (App Router, TypeScript), deployed on Vercel
- **Backend:** FastAPI + SQLAlchemy, deployed on Render (Docker, includes ffmpeg)
- **Database:** Postgres (Supabase)
- **Storage:** Supabase Storage (private bucket)
- **Background jobs:** Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`)

The full design write-up is on the in-app **/architecture** page.

## Repo layout

```
audio-notes/
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI routes (fast, synchronous work only)
│   │   ├── worker.py       # job queue + processing pipeline
│   │   ├── audio.py        # ffprobe validation, ffmpeg fixed-length splitting
│   │   ├── asr.py          # Gnani speech-to-text client with retries
│   │   ├── summarizer.py   # Gemini summary
│   │   ├── storage.py      # Supabase Storage REST calls
│   │   ├── db.py           # SQLAlchemy models: notes, chunks, jobs
│   │   └── config.py       # environment variables
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── page.tsx              # upload + past uploads
│   │   ├── notes/[id]/page.tsx   # progress, transcript, summary, retry
│   │   └── architecture/page.tsx # system explanation
│   ├── lib/api.ts                # API client + direct-to-bucket upload
│   └── .env.example
└── render.yaml
```

## Run locally

Requirements: Python 3.11+, Node 18+, ffmpeg, a Postgres database.

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in the values, then export them
uvicorn app.main:app --reload --port 8000

# frontend (new terminal)
cd frontend
npm install
cp .env.example .env.local
npm run dev                 # http://localhost:3000
```

## Deploy

1. **Supabase:** create a project. In Storage, create a **private** bucket named `audio`. Copy the Postgres connection string (use the *Session pooler* URI), the project URL and the `service_role` key.
2. **Keys:** get a Gnani API key from gnani.ai and a Gemini key from aistudio.google.com.
3. **Backend on Render:** New → Blueprint → select this repo (uses `render.yaml`). Fill in the env vars. Set `CORS_ORIGINS` to your Vercel URL.
4. **Frontend on Vercel:** import the repo, set **Root Directory** to `frontend`, add `NEXT_PUBLIC_API_URL` (the Render URL) and `NEXT_PUBLIC_GITHUB_URL`.
5. Add the Vercel URL to `CORS_ORIGINS` on Render and redeploy.

Note: Render's free tier sleeps after inactivity, so the first request can take ~30–50 s. The UI shows a "server waking up" message when that happens.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/notes` | Register an upload, returns `id` + signed upload URL |
| POST | `/api/notes/{id}/uploaded` | Confirm upload, enqueue processing |
| POST | `/api/notes/{id}/upload-failed` | Record a failed browser upload |
| GET | `/api/notes` | List past uploads |
| GET | `/api/notes/{id}` | Status, progress, transcript, summary, pieces |
| POST | `/api/notes/{id}/retry` | Retry a failed note |
| GET | `/api/health` | Health check |
