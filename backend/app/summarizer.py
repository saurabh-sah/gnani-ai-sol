"""Summary generation with Gemini's REST API (no SDK, so the request is easy to read)."""
import time

import httpx

from . import config


class SummaryError(Exception):
    pass


PROMPT = """You are summarising the transcript of an audio recording. The transcript came from
speech recognition, so it may have small errors and no punctuation. It may be in an Indian
language; write the summary in English.

Return Markdown with exactly these sections:
## Overview
Two or three sentences on what the recording is about.
## Key points
Bullet points of the most important information.
## Action items
Bullet points of tasks or follow-ups mentioned. Write "None mentioned" if there are none.

Transcript:
\"\"\"
{transcript}
\"\"\""""


def summarise(transcript: str) -> str:
    if not transcript.strip():
        return "_No speech was detected in this recording, so there is nothing to summarise._"

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent")
    payload = {
        "contents": [{"parts": [{"text": PROMPT.format(transcript=transcript)}]}],
        "generationConfig": {"temperature": 0.3},
    }
    last_error = ""
    for attempt in range(3):
        try:
            r = httpx.post(url, params={"key": config.GEMINI_API_KEY}, json=payload,
                           timeout=httpx.Timeout(10, read=120))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"network error: {exc.__class__.__name__}"
        else:
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    return "".join(p.get("text", "") for p in parts).strip()
                except (KeyError, IndexError):
                    raise SummaryError("The LLM returned an empty response (possibly blocked by safety filters).")
            last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(3 * (attempt + 1))
    raise SummaryError(f"Summary generation failed ({last_error})")
