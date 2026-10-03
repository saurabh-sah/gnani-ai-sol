"""Thin wrapper over the Supabase Storage REST API.

The browser uploads straight to Supabase using a signed upload URL, so large files
never pass through our API server (which has request size and timeout limits).
"""
from pathlib import Path
from urllib.parse import quote

import httpx

from . import config

_BASE = f"{config.SUPABASE_URL}/storage/v1"
_HEADERS = {
    "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
    "apikey": config.SUPABASE_SERVICE_KEY,
}


class StorageError(Exception):
    pass


def _object_path(path: str) -> str:
    return f"{config.SUPABASE_BUCKET}/{quote(path)}"


def create_signed_upload_url(path: str) -> str:
    """Returns a full URL the browser can PUT the file to (valid for 2 hours)."""
    try:
        r = httpx.post(f"{_BASE}/object/upload/sign/{_object_path(path)}", headers=_HEADERS, timeout=20)
    except httpx.HTTPError as exc:
        raise StorageError(f"cannot connect to storage at {config.SUPABASE_URL} ({exc.__class__.__name__})")
    if r.status_code >= 400:
        raise StorageError(f"Could not create upload URL ({r.status_code}): {r.text[:200]}")
    return f"{_BASE}{r.json()['url']}"


def object_exists(path: str) -> bool:
    try:
        r = httpx.head(f"{_BASE}/object/{_object_path(path)}", headers=_HEADERS, timeout=20)
    except httpx.HTTPError as exc:
        raise StorageError(f"cannot connect to storage ({exc.__class__.__name__})")
    return r.status_code == 200


def download_to(path: str, dest: Path) -> None:
    """Streams the object to disk so a large file is never held fully in memory."""
    with httpx.stream("GET", f"{_BASE}/object/{_object_path(path)}", headers=_HEADERS,
                      timeout=httpx.Timeout(30, read=300)) as r:
        if r.status_code >= 400:
            raise StorageError(f"Download failed ({r.status_code})")
        with dest.open("wb") as f:
            for block in r.iter_bytes(1024 * 1024):
                f.write(block)
