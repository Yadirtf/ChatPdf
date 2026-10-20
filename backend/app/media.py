"""Audio y video: transcripción local con faster-whisper y fotogramas clave con PyAV.

PyAV trae FFmpeg dentro de la rueda de Python, así que no hace falta instalarlo aparte.
"""

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import av

from .config import settings
from .imaging import Picture, difference, prepare, signature


class MediaError(ValueError):
    pass


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    has_video: bool


@dataclass
class Segment:
    start: float
    end: float
    text: str


def probe(path: Path) -> MediaInfo:
    try:
        with av.open(str(path)) as c:
            # Las carátulas de los MP3 aparecen como una pista de video de un solo cuadro.
            video = [s for s in c.streams.video if not (s.disposition & av.stream.Disposition.attached_pic)]
            duration = c.duration / av.time_base if c.duration else 0.0
            if not duration:
                duration = max((float(s.duration * s.time_base) for s in c.streams if s.duration), default=0.0)
            return MediaInfo(duration, bool(c.streams.audio), bool(video))
    except av.FFmpegError as e:
        raise MediaError(f"No se pudo leer el archivo multimedia: {e}") from e


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------- Transcripción ----------

_whisper = None
_whisper_lock = threading.Lock()


def _model():
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel

            # int8 cabe en poca memoria y en CPU es varias veces más rápido que float32.
            _whisper = WhisperModel(settings.whisper_model, device=settings.whisper_device, compute_type="int8")
        return _whisper


def whisper_loaded() -> bool:
    return _whisper is not None


def transcribe(path: Path, duration: float, on_progress: Callable[[float], None]) -> tuple[list[Segment], str]:
    segments, info = _model().transcribe(
        str(path),
        language=settings.whisper_language or None,
        vad_filter=True,                    # salta silencios: más rápido y sin alucinaciones
        condition_on_previous_text=False,   # evita que un error se repita en bucle
    )
    out = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append(Segment(seg.start, seg.end, text))
        if duration:
            on_progress(min(1.0, seg.end / duration))
    return out, info.language


def group_segments(segments: list[Segment], size: int) -> list[Segment]:
    """Une segmentos consecutivos hasta ~size caracteres; cada grupo repite el último del anterior."""
    groups: list[Segment] = []
    current: list[Segment] = []
    for seg in segments:
        if current and sum(len(s.text) + 1 for s in current) + len(seg.text) > size:
            groups.append(Segment(current[0].start, current[-1].end, " ".join(s.text for s in current)))
            current = current[-1:] if len(current) > 1 else []
        current.append(seg)
    if current:
        groups.append(Segment(current[0].start, current[-1].end, " ".join(s.text for s in current)))
    return groups


def transcript_around(segments: list[Segment], t: float, window: float = 15) -> str:
    return " ".join(s.text for s in segments if s.end >= t - window and s.start <= t + window)[:600]


# ---------- Fotogramas ----------

SCENE_CHANGE = 14.0   # diferencia media de píxel (0–255) a partir de la cual hay una escena nueva


def _distinct(frames: list[tuple[float, "av.VideoFrame"]], kept: list[tuple[float, object, Picture]],
              context: Callable[[float], str]):
    for t, frame in frames:
        img = frame.to_image()
        sig = signature(img)
        if any(difference(sig, k[1]) < SCENE_CHANGE for k in kept):
            continue
        pic = prepare(img, context(t), "frame")
        if pic:
            kept.append((t, sig, pic))


def keyframes(path: Path, duration: float, max_frames: int, context: Callable[[float], str]) -> list[tuple[float, Picture]]:
    """Fotogramas representativos y distintos entre sí, en orden temporal.

    Primero se decodifican solo los cuadros clave (rápido, y los codificadores los ponen en los
    cambios de escena). Si el video tiene pocos, como una grabación de pantalla, se completan con
    muestras repartidas en el tiempo.
    """
    target = min(max_frames, max(1, math.ceil(duration / 12)))
    kept: list[tuple[float, object, Picture]] = []
    try:
        with av.open(str(path)) as c:
            stream = c.streams.video[0]
            stream.codec_context.skip_frame = "NONKEY"
            frames = ((f.time or 0.0, f) for f in c.decode(stream))
            _distinct(frames, kept, context)

        if len(kept) < target and duration:
            with av.open(str(path)) as c:
                stream = c.streams.video[0]
                for i in range(target):
                    t = (i + 0.5) * duration / target
                    c.seek(int(t / stream.time_base), stream=stream)
                    frame = next((f for f in c.decode(stream) if (f.time or 0) >= t - 0.5), None)
                    if frame is not None:
                        _distinct([(t, frame)], kept, context)
    except av.FFmpegError as e:
        raise MediaError(f"No se pudo decodificar el video: {e}") from e

    kept.sort(key=lambda k: k[0])
    if len(kept) > max_frames:
        step = len(kept) / max_frames
        kept = [kept[int(i * step)] for i in range(max_frames)]
    return [(t, pic) for t, _, pic in kept]
