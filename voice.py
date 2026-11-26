"""Offline speech-to-text for LOKI (faster-whisper, runs on your CPU).

The desktop panel records your voice, sends raw 16 kHz 16-bit audio to
POST /voice/transcribe, and gets text back. Nothing leaves your laptop.
"""
import threading

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

# ---------- Settings ----------
WHISPER_MODEL = "base"      # "tiny" = fastest, "base" = balanced, "small" = more accurate but slower
WHISPER_LANGUAGE = "en"     # None = auto-detect (slightly slower, needed for Hindi/Gujarati)
INITIAL_PROMPT = "Loki. Add a task. Remind me. Log my habit. What is the weather?"  # nudges it toward your usual words

router = APIRouter(prefix="/voice")

_model = None
_lock = threading.Lock()


def _load():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel  # imported here so the backend runs without it

            _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe_pcm16(raw: bytes) -> str:
    raw = raw[: len(raw) // 2 * 2]
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) < 16000 * 0.3:  # shorter than 0.3 s: nothing to transcribe
        return ""
    model = _load()
    segments, _info = model.transcribe(
        audio,
        language=WHISPER_LANGUAGE,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=INITIAL_PROMPT,
    )
    return " ".join(s.text.strip() for s in segments).strip()


@router.post("/transcribe")
async def transcribe(request: Request):
    raw = await request.body()
    try:
        text = await run_in_threadpool(transcribe_pcm16, raw)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="faster-whisper is not installed. Run: pip install faster-whisper",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"text": text}


@router.get("/status")
def status():
    return {"model": WHISPER_MODEL, "language": WHISPER_LANGUAGE, "loaded": _model is not None}


def _warm():
    """Load the model in the background at startup so your first voice command isn't slow."""
    try:
        _load()
        print(f"[voice] Whisper '{WHISPER_MODEL}' ready.")
    except ImportError:
        print("[voice] faster-whisper not installed: voice input disabled (pip install faster-whisper)")
    except Exception as e:
        print(f"[voice] could not load Whisper: {e}")


threading.Thread(target=_warm, daemon=True).start()
