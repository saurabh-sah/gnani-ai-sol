"""Gnani (Vachana) speech-to-text client for a single short WAV piece.

Docs: https://docs.gnani.ai/api/STT/speech-to-text
POST multipart/form-data with `audio_file` and `language_code`; header `X-API-Key-ID`.
One request accepts at most 60 seconds of audio, which is why we chunk.
"""
import time
from pathlib import Path

import httpx

from . import config


class ASRError(Exception):
    pass


# Status codes worth retrying: rate limit and temporary server errors.
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def transcribe_piece(path: Path, language: str, max_tries: int = 4) -> str:
    delay = 2.0
    last_error = ""
    for attempt in range(1, max_tries + 1):
        try:
            with path.open("rb") as f:
                r = httpx.post(
                    config.GNANI_STT_URL,
                    headers={"X-API-Key-ID": config.GNANI_API_KEY},
                    data={"language_code": language, "format": "transcribe"},
                    files={"audio_file": (path.name, f, "audio/wav")},
                    timeout=httpx.Timeout(10, read=90),
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"network error: {exc.__class__.__name__}"
        else:
            if r.status_code == 200:
                body = r.json()
                if body.get("success") is False:
                    raise ASRError(f"ASR rejected the audio: {body}")
                return (body.get("transcript") or "").strip()
            if r.status_code in (401, 403):
                raise ASRError("ASR API key is invalid or out of credits.")
            last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in _RETRYABLE:
                raise ASRError(last_error)

        if attempt < max_tries:
            time.sleep(delay)
            delay *= 2  # exponential backoff: 2s, 4s, 8s
    raise ASRError(f"ASR failed after {max_tries} tries ({last_error})")
