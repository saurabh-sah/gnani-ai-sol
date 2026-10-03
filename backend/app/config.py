"""All settings come from environment variables so the same code runs locally and on Render."""
import os


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


DATABASE_URL = _env("DATABASE_URL", required=True)

# Supabase Storage (bucket must exist and be private)
# Accept common copy-paste mistakes: spaces/quotes, or a storage/S3 endpoint instead of the project URL.
SUPABASE_URL = _env("SUPABASE_URL", required=True).strip().strip('"\'').split("/storage/v1")[0].rstrip("/")
SUPABASE_URL = SUPABASE_URL.replace(".storage.supabase.co", ".supabase.co")
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY", required=True)
SUPABASE_BUCKET = _env("SUPABASE_BUCKET", "audio")

# Gnani / Vachana speech-to-text
GNANI_API_KEY = _env("GNANI_API_KEY", required=True)
GNANI_STT_URL = _env("GNANI_STT_URL", "https://api.vachana.ai/stt/v3")

# Gemini for summaries
GEMINI_API_KEY = _env("GEMINI_API_KEY", required=True)
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")

# Comma separated list of allowed frontend origins for CORS
CORS_ORIGINS = [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# Gnani accepts at most 60s per request and recommends <=30s, so we cut 25s pieces.
CHUNK_SECONDS = int(_env("CHUNK_SECONDS", "25"))
ASR_PARALLELISM = int(_env("ASR_PARALLELISM", "3"))
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "500"))
MAX_JOB_ATTEMPTS = int(_env("MAX_JOB_ATTEMPTS", "3"))

# On free hosting a separate worker service costs money, so the API process can
# also run the worker loop in a background thread. Set to "false" when you run
# `python -m app.worker` as its own service.
RUN_WORKER_IN_API = _env("RUN_WORKER_IN_API", "true").lower() == "true"

SUPPORTED_LANGUAGES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}
