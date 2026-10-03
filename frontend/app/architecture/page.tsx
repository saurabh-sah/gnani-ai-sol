import { GITHUB_URL } from "@/lib/api";

export const metadata = { title: "Architecture · Audio Notes" };

export default function Architecture() {
  return (
    <article className="prose">
      <h1>How this works</h1>
      <p className="muted">
        Source code: <a href={GITHUB_URL} target="_blank" rel="noreferrer">{GITHUB_URL}</a>
      </p>

      <section className="card">
        <h2>Components</h2>
        <ul>
          <li><strong>Frontend</strong> — Next.js (App Router) on Vercel.</li>
          <li><strong>API</strong> — FastAPI on Render. Only does fast work: validates requests, talks to the database and storage.</li>
          <li><strong>Worker</strong> — a Python loop that pulls jobs from a Postgres table and does the slow work (ffmpeg, Gnani ASR, Gemini).</li>
          <li><strong>Database</strong> — Postgres (Supabase). Tables: <code>notes</code>, <code>chunks</code>, <code>jobs</code>.</li>
          <li><strong>Storage</strong> — a private Supabase Storage bucket holding the original audio files.</li>
        </ul>
      </section>

      <section className="card">
        <h2>Flow from upload to transcript</h2>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, background: "#f2f2ef", padding: 12, borderRadius: 8 }}>
{`Browser ──POST /api/notes──▶ API ──▶ creates note row + signed upload URL
Browser ──PUT file (with progress)──▶ Supabase Storage   (file skips our API)
Browser ──POST /api/notes/{id}/uploaded──▶ API checks object exists, inserts job row
Worker  ──claims job (FOR UPDATE SKIP LOCKED)
        ──download ▶ ffprobe check ▶ ffmpeg → 25 s WAV pieces
        ──Gnani STT per piece (3 in parallel, retries) ▶ chunk rows
        ──join pieces ▶ Gemini summary ▶ note = completed
Browser ──polls GET /api/notes/{id} every 2 s──▶ stage + progress %`}
        </pre>
        <ol>
          <li>The browser asks the API for a signed upload URL. The API creates a <code>notes</code> row with status <code>awaiting_upload</code>.</li>
          <li>The browser uploads the file directly to the storage bucket with <code>XMLHttpRequest</code>, which gives real upload progress. Large files never go through the API server, so its request-size and timeout limits don&apos;t matter.</li>
          <li>The browser tells the API the upload finished. The API confirms the object really exists in the bucket, sets the note to <code>queued</code> and inserts a row into <code>jobs</code>.</li>
          <li>The worker claims the job, downloads the file, checks it with <code>ffprobe</code>, splits it with <code>ffmpeg</code>, transcribes each piece with Gnani, joins the text and asks Gemini for a summary.</li>
          <li>After every step the worker writes <code>stage</code> and <code>progress</code> on the note. The note page polls every 2 seconds and renders whatever is there.</li>
        </ol>
      </section>

      <section className="card">
        <h2>Where files live</h2>
        <p>
          The original upload stays in the private bucket at <code>audio/&lt;note-id&gt;/&lt;filename&gt;</code> so a failed job can
          be retried without re-uploading. Converted WAV pieces exist only in a temp folder on the worker during processing and
          are deleted afterwards. Transcript text (whole and per piece) and the summary are stored in Postgres.
        </p>
      </section>

      <section className="card">
        <h2>Handling long audio</h2>
        <p>
          Gnani&apos;s speech-to-text API accepts at most 60 seconds per request and recommends 30 seconds or less. The worker
          converts the file to 16 kHz mono WAV and cuts it into fixed 25-second pieces in a single ffmpeg pass
          (the segment muxer). Each piece is sent separately, three at a time, and stored as its own row in <code>chunks</code>
          with its start time, so the transcript can show timestamps.
        </p>
        <p>
          Because each piece is saved as soon as it is transcribed, a retry only redoes the pieces that failed instead of the
          whole file. The trade-off of fixed-length cuts is that a word sitting exactly on a boundary can be split; cutting on
          silence would fix that (see below).
        </p>
      </section>

      <section className="card">
        <h2>Synchronous vs background</h2>
        <p><strong>Synchronous (in the request):</strong> creating the note, issuing the signed URL, confirming the upload,
          listing notes, reading a note, triggering a retry. All of these finish in milliseconds.</p>
        <p><strong>Background (worker):</strong> downloading, validating, splitting, ASR calls, summarisation. These can take
          minutes for long recordings, so they never block an HTTP request.</p>
        <p>
          The queue is a Postgres table. The worker claims a job with <code>SELECT … FOR UPDATE SKIP LOCKED</code>, so several
          workers can run at once without picking the same job. I chose this over Celery + Redis because Postgres is already
          required, so it&apos;s one less service to deploy and monitor. On free hosting the worker loop runs as a thread inside the
          API process (<code>RUN_WORKER_IN_API=true</code>); the same code runs as a separate service with
          <code> python -m app.worker</code>.
        </p>
      </section>

      <section className="card">
        <h2>Failure handling</h2>
        <ul>
          <li><strong>Upload fails</strong> (network drop, storage rejects): the upload form shows the error, and the note is marked failed.</li>
          <li><strong>Corrupt or non-audio file</strong>: caught by <code>ffprobe</code>; marked failed immediately with a clear message, no retries.</li>
          <li><strong>ASR timeout / 429 / 5xx</strong>: each piece retries up to 4 times with exponential backoff (2s, 4s, 8s).</li>
          <li><strong>Whole job fails</strong>: the job is re-queued up to 3 times with a growing delay; the page shows
            &quot;Attempt N failed, retrying in X s&quot; and the error. After the last attempt the note is failed and a Retry button appears.</li>
          <li><strong>Invalid API key / no credits</strong>: treated as permanent so it doesn&apos;t burn retries.</li>
          <li><strong>Worker crash mid-job</strong>: jobs stuck in <code>running</code> for 15 minutes are put back in the queue.</li>
          <li><strong>Backend unreachable</strong>: the page keeps polling and shows a connection warning instead of freezing.</li>
        </ul>
      </section>

      <section className="card">
        <h2>What I&apos;d do with more time</h2>
        <ul>
          <li>Split on silence (or with a small overlap) instead of fixed 25 s cuts, so words aren&apos;t broken at boundaries.</li>
          <li>Use Gnani&apos;s streaming/WebSocket API to show the transcript live while it&apos;s being produced.</li>
          <li>Push progress over Server-Sent Events instead of polling.</li>
          <li>Run the worker as its own service and scale it separately from the API.</li>
          <li>For very long transcripts, summarise in sections and then summarise the summaries.</li>
          <li>Add user accounts so each person only sees their own uploads; add Alembic migrations and tests.</li>
          <li>Auto-detect language instead of asking the user.</li>
        </ul>
      </section>
    </article>
  );
}
