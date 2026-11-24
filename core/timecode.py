import os
import re
import struct
from typing import Any, Tuple


DEFAULT_SHOT_SEPARATOR = "_"


def split_shot_id(shot_id: str) -> Tuple[str, str]:
    """Splits a Shot ID like 'R01_0010' or legacy 'R01-0010' into (seq, shot)."""
    if not shot_id:
        return "", ""
    shot_id = shot_id.strip()
    if "_" in shot_id:
        parts = shot_id.split("_", 1)
        return parts[0].strip(), parts[1].strip()
    if "-" in shot_id:
        parts = shot_id.split("-", 1)
        return parts[0].strip(), parts[1].strip()
    return shot_id, "0010"


def format_shot_id(seq: str, shot: Any, separator: str = DEFAULT_SHOT_SEPARATOR) -> str:
    """Formats sequence and shot into a standardized Shot ID (e.g. 'R01_0010')."""
    seq = str(seq or "").strip()
    shot = str(shot or "").strip()
    if not seq and not shot:
        return ""
    if shot.isdigit():
        shot = f"{int(shot):04d}"
    else:
        m = re.match(r"^(\d+)(.*)$", shot)
        if m:
            shot = f"{int(m.group(1)):04d}{m.group(2)}"
    if not seq:
        return shot
    if not shot:
        return seq
    return f"{seq}{separator}{shot}"


def normalize_shot_id(shot_id: str, separator: str = DEFAULT_SHOT_SEPARATOR) -> str:
    """Normalizes any Shot ID string (e.g. 'UHC-150' or 'UHC_0150') to canonical format ('UHC_0150')."""
    if not shot_id:
        return ""
    raw = str(shot_id).strip()
    if not raw:
        return ""
    if "_" not in raw and "-" not in raw:
        return raw
    seq, shot = split_shot_id(raw)
    return format_shot_id(seq, shot, separator=separator)


def frame_to_timecode(frame_number: int, fps: float = 24.0) -> str:
    """Convert a frame number to non-drop timecode (HH:MM:SS:FF)."""
    fps_int = max(1, int(round(float(fps))))
    frame_number = int(frame_number)
    total_seconds = frame_number // fps_int
    frames = frame_number % fps_int
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def timecode_to_frame(tc: str, fps: float = 24.0) -> int:
    """Convert a timecode string (HH:MM:SS:FF) to frame count."""
    if not tc:
        return 0
    try:
        fps_int = max(1, int(round(float(fps))))
        parts = tc.strip().replace(";", ":").split(":")
        if len(parts) != 4:
            return 0
        h, m, s, f = [int(p) for p in parts]
        return (h * 3600 + m * 60 + s) * fps_int + f
    except Exception:
        return 0


def parse_clip_seq_shot(clip_name: str, default_seq: str = "R01", default_shot_num: int = 10) -> Tuple[str, str]:
    """
    Attempt to extract Sequence and Shot from a clip name (e.g. 'R01_0010_plate.mov' or 'R01-0010_plate.mov').
    Falls back to (default_seq, f'{default_shot_num:04d}').
    """
    if not clip_name:
        return default_seq, f"{default_shot_num:04d}"
    base = os.path.splitext(clip_name)[0]
    m = re.match(r"^([A-Za-z0-9]+)[-_](\d{3,4}[A-Za-z]?)(?:[-_.]|$)", base)
    if m:
        return m.group(1), m.group(2)
    return default_seq, f"{default_shot_num:04d}"


def get_png_dimensions(png_path: str) -> Tuple[int, int]:
    """Reads width and height from a PNG header without requiring PIL/Pillow."""
    try:
        with open(png_path, "rb") as f:
            head = f.read(24)
            if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
    except Exception:
        pass
    return 120, 68
