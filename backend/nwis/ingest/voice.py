"""Optional on-prem speech-to-text for expert voice memos (Assamese, Hindi, English).

Uses faster-whisper (CTranslate2 Whisper) when installed: `pip install "nwis[asr]"`. Nothing leaves the
server. Model weights are downloaded once by faster-whisper and cached; for an air-gapped deployment,
copy the cache (or a local model directory in NWIS_ASR_MODEL) onto the server beforehand.

The memo is transcribed twice when it is not in English: once in the original language (kept on the
document for the record) and once with Whisper's translate task, because the extraction lexicon is
English. Whisper's Assamese accuracy is limited out of the box; fine-tuned models exist (arXiv 2607.17164)
and can be dropped in through NWIS_ASR_MODEL.
"""
from __future__ import annotations

from pathlib import Path

from .. import config

_model = None


def asr_status() -> dict:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return {"available": False, "model": None,
                "hint": 'Voice memos need the optional speech-to-text extra: pip install "nwis[asr]" '
                        "(then cache the Whisper model on the server). Typed memos work without it."}
    return {"available": True, "model": config.ASR_MODEL, "hint": None}


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.ASR_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(path: Path, language: str | None = None) -> dict:
    """Returns {language, original, english}. `language` is an ISO code hint ('as', 'hi', 'en') or None to detect."""
    model = _get_model()
    segs, info = model.transcribe(str(path), language=language, task="transcribe", vad_filter=True)
    original = " ".join(s.text.strip() for s in segs).strip()
    lang = info.language
    english = original
    if lang != "en":
        segs, _ = model.transcribe(str(path), language=lang, task="translate", vad_filter=True)
        english = " ".join(s.text.strip() for s in segs).strip()
    return {"language": lang, "language_probability": round(float(info.language_probability), 3),
            "original": original, "english": english}
