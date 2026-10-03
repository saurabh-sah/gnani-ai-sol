"""ffmpeg helpers: validate the file and cut it into fixed-length WAV pieces."""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class CorruptAudioError(Exception):
    """Raised when ffprobe/ffmpeg cannot read the file as audio."""


@dataclass
class Piece:
    index: int
    path: Path
    start: float
    end: float


def probe_duration(path: Path) -> float:
    """Returns duration in seconds, or raises CorruptAudioError if there is no readable audio stream."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
           "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise CorruptAudioError("The file could not be read as audio. It may be corrupted or in an unsupported format.")
    info = json.loads(result.stdout or "{}")
    if not info.get("streams"):
        raise CorruptAudioError("No audio track was found in this file.")
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, ValueError):
        raise CorruptAudioError("Could not determine the length of this audio file.")
    if duration <= 0.2:
        raise CorruptAudioError("The audio is empty or too short to transcribe.")
    return duration


def split_fixed(path: Path, out_dir: Path, chunk_seconds: int, duration: float) -> list[Piece]:
    """Converts to 16 kHz mono WAV (what ASR models expect) and cuts it every `chunk_seconds`.

    The ffmpeg segment muxer does both in one pass, so a 1-hour file is not decoded repeatedly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "piece_%04d.wav"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", str(chunk_seconds),
           "-reset_timestamps", "1", str(pattern)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise CorruptAudioError(f"Audio conversion failed: {result.stderr.strip()[:300]}")

    files = sorted(out_dir.glob("piece_*.wav"))
    if not files:
        raise CorruptAudioError("Audio conversion produced no output.")

    pieces = []
    for i, f in enumerate(files):
        start = i * chunk_seconds
        pieces.append(Piece(index=i, path=f, start=float(start), end=float(min(start + chunk_seconds, duration))))
    return pieces
