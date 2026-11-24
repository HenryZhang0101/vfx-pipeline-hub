"""
Pure-Python, UI-free thumbnail generation engine for VFX shots and media versions.

Storage layout:
  - Shot thumbnails:  <vfx_root>/_thumbnails/<shot_id>.png
  - Media thumbnails: <vfx_root>/_thumbnails/media/<shot_id>/<category>_<identifier>_<version>.png
"""

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from config import (
    MEDIA_EXTENSIONS,
    SEQUENCE_EXTENSIONS,
    get_ffmpeg_bin,
    get_ffprobe_bin,
)
from core.models import Project, Shot
from core.timecode import format_shot_id, split_shot_id

DEFAULT_THUMB_WIDTH = 480
PILLOW_DIRECT_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MOVIE_EXTS = {".mov", ".mp4", ".mxf"}


def _subprocess_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _sanitize_token(token: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(token or "").strip())
    return cleaned.strip("_") or "item"


def get_shot_thumbnail_path(project: Project, shot_id: str) -> str:
    """Returns canonical shot thumbnail path: <vfx_root>/_thumbnails/<shot_id>.png."""
    if not project or not project.thumbnails_dir or not shot_id:
        return ""
    seq, shot = split_shot_id(shot_id)
    norm_id = format_shot_id(seq, shot) if (seq and shot) else shot_id.strip()
    return os.path.normpath(os.path.join(project.thumbnails_dir, f"{norm_id}.png"))


def find_existing_shot_thumbnail(project: Project, shot_id: str) -> str:
    """Returns existing shot thumbnail path, checking canonical '_' ID first and legacy '-' ID as fallback."""
    canonical = get_shot_thumbnail_path(project, shot_id)
    if canonical and os.path.isfile(canonical) and os.path.getsize(canonical) > 0:
        return canonical
    if project and project.thumbnails_dir and shot_id:
        seq, shot = split_shot_id(shot_id)
        if seq and shot:
            legacy = os.path.normpath(os.path.join(project.thumbnails_dir, f"{format_shot_id(seq, shot, separator='-')}.png"))
            if legacy != canonical and os.path.isfile(legacy) and os.path.getsize(legacy) > 0:
                return legacy
    return ""


def get_media_thumbnail_path(
    project: Project,
    shot_id: str,
    category: str,
    identifier: str,
    version: str,
) -> str:
    """Returns canonical media thumbnail path under <vfx_root>/_thumbnails/media/<shot_id>/."""
    if not project or not project.thumbnails_dir or not shot_id:
        return ""
    seq, shot = split_shot_id(shot_id)
    norm_id = format_shot_id(seq, shot) if (seq and shot) else shot_id.strip()
    cat_tok = _sanitize_token(category or "media")
    id_tok = _sanitize_token(identifier or "plate")
    ver_tok = _sanitize_token(version or "v0001")
    return os.path.normpath(
        os.path.join(
            project.thumbnails_dir,
            "media",
            norm_id,
            f"{cat_tok}_{id_tok}_{ver_tok}.png",
        )
    )


def _probe_video_duration(movie_path: str) -> float:
    """Probes movie duration in seconds using ffprobe. Returns 0.0 on failure."""
    ffprobe_exe = get_ffprobe_bin()
    if not ffprobe_exe or (not os.path.isfile(ffprobe_exe) and not shutil.which(ffprobe_exe)):
        return 0.0
    cmd = [
        ffprobe_exe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        movie_path,
    ]
    kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": 8}
    if sys.platform == "win32":
        kwargs["creationflags"] = _subprocess_flags()
    try:
        res = subprocess.run(cmd, **kwargs)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            dur = (data.get("format") or {}).get("duration")
            if dur:
                return max(0.0, float(dur))
            for st in data.get("streams") or []:
                if st.get("codec_type") == "video" and st.get("duration"):
                    return max(0.0, float(st["duration"]))
    except Exception:
        pass
    return 0.0


def _extract_with_pillow(source_path: str, tmp_png: str, width: int) -> bool:
    """Fast 8-bit image thumbnail extraction via Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        with Image.open(source_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            elif img.mode == "RGBA":
                bg = Image.new("RGB", img.size, (25, 25, 25))
                bg.paste(img, mask=img.split()[3])
                img = bg

            w, h = img.size
            if w <= 0 or h <= 0:
                return False
            if w > width:
                new_h = max(2, int(round(h * (width / float(w)))))
                if new_h % 2 != 0:
                    new_h += 1
                resample = getattr(Image, "Resampling", Image).LANCZOS
                img = img.resize((width, new_h), resample)
            img.save(tmp_png, format="PNG", optimize=True)
            return os.path.isfile(tmp_png) and os.path.getsize(tmp_png) > 0
    except Exception:
        return False


def _run_ffmpeg_frame(
    ffmpeg_exe: str,
    source_path: str,
    tmp_png: str,
    vf: str,
    seek_sec: Optional[float] = None,
) -> bool:
    cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]
    if seek_sec is not None and seek_sec > 0.0:
        cmd.extend(["-ss", f"{seek_sec:.3f}"])
    # NOTE: Never pass -apply_trc iec61966_2_1 before EXR inputs (rejected by bundled ffmpeg.exe).
    cmd.extend([
        "-i", source_path,
        "-frames:v", "1",
        "-vf", vf,
        tmp_png,
    ])
    kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": 20}
    if sys.platform == "win32":
        kwargs["creationflags"] = _subprocess_flags()
    try:
        res = subprocess.run(cmd, **kwargs)
        return res.returncode == 0 and os.path.isfile(tmp_png) and os.path.getsize(tmp_png) > 0
    except Exception:
        return False


def extract_thumbnail_png(
    source_path: str,
    output_png: str,
    width: int = DEFAULT_THUMB_WIDTH,
    is_movie: bool = False,
) -> bool:
    """
    Extracts a resized 8-bit PNG thumbnail from an image or movie file.
    Writes atomically via a temporary file and os.replace().
    """
    if not source_path or not os.path.isfile(source_path) or not output_png:
        return False

    out_dir = os.path.dirname(os.path.abspath(output_png))
    os.makedirs(out_dir, exist_ok=True)
    tmp_png = f"{output_png}.tmp.{os.getpid()}.png"

    ext = os.path.splitext(source_path)[1].lower()
    ok = False
    try:
        # 1. Fast path for standard 8-bit image formats via Pillow
        if not is_movie and ext in PILLOW_DIRECT_EXTS:
            ok = _extract_with_pillow(source_path, tmp_png, width)

        # 2. FFmpeg path for EXR, DPX, movies, or Pillow fallback
        if not ok:
            ffmpeg_exe = get_ffmpeg_bin()
            if ffmpeg_exe and (os.path.isfile(ffmpeg_exe) or shutil.which(ffmpeg_exe)):
                scale_expr = f"scale='min({width},iw)':-2:flags=lanczos"
                if ext == ".exr":
                    # Gentle gamma lift so linear EXRs are legible as sRGB thumbnails
                    vf_primary = f"{scale_expr},eq=gamma=2.2"
                    vf_fallback = scale_expr
                else:
                    vf_primary = scale_expr
                    vf_fallback = scale_expr

                seek_candidates: List[Optional[float]] = [None]
                if is_movie or ext in MOVIE_EXTS:
                    dur = _probe_video_duration(source_path)
                    if dur > 0.2:
                        seek_candidates = [dur * 0.5, dur * 0.25, None]

                for seek in seek_candidates:
                    if _run_ffmpeg_frame(ffmpeg_exe, source_path, tmp_png, vf_primary, seek_sec=seek):
                        ok = True
                        break
                    if vf_fallback != vf_primary:
                        if _run_ffmpeg_frame(ffmpeg_exe, source_path, tmp_png, vf_fallback, seek_sec=seek):
                            ok = True
                            break

        if ok and os.path.isfile(tmp_png):
            os.replace(tmp_png, output_png)
            return True
        return False
    finally:
        if os.path.isfile(tmp_png):
            try:
                os.remove(tmp_png)
            except OSError:
                pass


def pick_representative_frame(media_dict: Dict[str, Any]) -> Tuple[str, bool]:
    """
    Chooses a representative source file for a scanned media item.
    Returns (file_path, is_movie).
    For image sequences, picks the middle frame to avoid black frames / slates at frame 1001.
    """
    if not media_dict or not media_dict.get("is_playable", False):
        return "", False

    ext = (media_dict.get("ext") or "").lower()
    if media_dict.get("is_sequence"):
        folder = media_dict.get("folder_path") or media_dict.get("version_dir") or ""
        first_file = media_dict.get("first_file_path") or ""
        if folder:
            try:
                seq_files: List[str] = []
                with os.scandir(folder) as it:
                    for entry in it:
                        try:
                            if not entry.is_file():
                                continue
                        except OSError:
                            continue
                        f = entry.name
                        dot_idx = f.rfind(".")
                        f_ext = f[dot_idx:].lower() if dot_idx != -1 else ""
                        if f_ext in SEQUENCE_EXTENSIONS and (not ext or f_ext == ext):
                            seq_files.append(f)
                if seq_files:
                    seq_files.sort()
                    mid_file = seq_files[len(seq_files) // 2]
                    return os.path.join(folder, mid_file), False
            except OSError:
                pass
        return (first_file, False) if first_file and os.path.isfile(first_file) else ("", False)

    first_file = media_dict.get("first_file_path") or ""
    if first_file and os.path.isfile(first_file):
        file_ext = os.path.splitext(first_file)[1].lower()
        if file_ext in MEDIA_EXTENSIONS:
            return first_file, file_ext in MOVIE_EXTS
    return "", False


def generate_media_thumbnail(
    project: Project,
    media_dict: Dict[str, Any],
    force: bool = False,
    width: int = DEFAULT_THUMB_WIDTH,
) -> Optional[str]:
    """Generates (or returns cached) PNG thumbnail for a single scanned media version."""
    if not project or not media_dict or not media_dict.get("is_playable", False):
        return None

    out_png = media_dict.get("thumb_path") or get_media_thumbnail_path(
        project,
        media_dict.get("shot_id", ""),
        media_dict.get("category", "media"),
        media_dict.get("identifier", "plate"),
        media_dict.get("version", "v0001"),
    )
    if not out_png:
        return None

    if not force and os.path.isfile(out_png) and os.path.getsize(out_png) > 0:
        return out_png

    src_path, is_movie = pick_representative_frame(media_dict)
    if not src_path:
        return None

    if extract_thumbnail_png(src_path, out_png, width=width, is_movie=is_movie):
        media_dict["thumb_path"] = out_png
        media_dict["has_thumb"] = True
        return out_png
    return None


def pick_best_shot_media(media_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Selects the best playable media item to represent a shot:
      1. Latest 'comp'
      2. Latest 'plate'
      3. Latest playable 'media' item
    """
    playable = [m for m in media_items if m.get("is_playable")]
    if not playable:
        return None

    for preferred_id in ("comp", "plate"):
        matches = [
            m for m in playable
            if (m.get("identifier") or "").strip().lower() == preferred_id
        ]
        if matches:
            matches.sort(key=lambda m: (m.get("version") or "").lower(), reverse=True)
            return matches[0]

    media_only = [m for m in playable if m.get("category") == "media"]
    pool = media_only or playable
    pool.sort(key=lambda m: (m.get("version") or "").lower(), reverse=True)
    return pool[0]


def _fast_pick_shot_media(project: Project, repo: Any, shot_id: str) -> Optional[Dict[str, Any]]:
    """
    Fast-path lookup for a shot's thumbnail source: checks media/comp and media/plate
    latest versions directly before falling back to a full shot media scan.
    """
    if not project or not repo or not shot_id:
        return None
    shot_dir = repo.get_shot_dir(project, shot_id)
    media_root = os.path.join(shot_dir, "media")
    if os.path.isdir(media_root):
        for preferred_id in ("comp", "plate"):
            id_dir = os.path.join(media_root, preferred_id)
            if not os.path.isdir(id_dir):
                continue
            v_dirs: List[Tuple[str, str]] = []
            try:
                with os.scandir(id_dir) as it:
                    for entry in it:
                        if re.match(r"^v\d+", entry.name, re.IGNORECASE):
                            try:
                                if entry.is_dir():
                                    v_dirs.append((entry.name, entry.path))
                            except OSError:
                                continue
            except OSError:
                continue
            v_dirs.sort(key=lambda x: x[0].lower(), reverse=True)
            for v_name, v_path in v_dirs:
                info = repo.inspect_media_folder(v_path)
                if info and info.get("is_playable"):
                    seq, sh = split_shot_id(shot_id)
                    norm_sid = format_shot_id(seq, sh) if (seq and sh) else shot_id.strip()
                    m_thumb = get_media_thumbnail_path(project, norm_sid, "media", preferred_id, v_name)
                    out = dict(info)
                    out.update({
                        "shot_id": norm_sid,
                        "category": "media",
                        "identifier": preferred_id,
                        "version": v_name,
                        "version_dir": os.path.normpath(v_path),
                        "thumb_path": m_thumb,
                    })
                    return out
    all_media = repo.scan_all_shot_media(project, shot_id)
    return pick_best_shot_media(all_media)


def generate_shot_thumbnail(
    project: Project,
    repo: Any,
    shot: Shot,
    force: bool = False,
    media_dict: Optional[Dict[str, Any]] = None,
    width: int = DEFAULT_THUMB_WIDTH,
) -> Optional[str]:
    """
    Generates (or returns cached) shot thumbnail at <vfx_root>/_thumbnails/<shot_id>.png.
    Reuses the chosen media item's thumbnail when available, updating shot.thumb.
    """
    if not project or not shot or not shot.shot_id:
        return None

    shot_png = get_shot_thumbnail_path(project, shot.shot_id)
    if not shot_png:
        return None

    if not force:
        existing_png = find_existing_shot_thumbnail(project, shot.shot_id)
        if existing_png:
            shot.thumb = existing_png
            return existing_png

    chosen = media_dict
    if chosen is None and repo is not None:
        chosen = _fast_pick_shot_media(project, repo, shot.shot_id)

    if chosen is None:
        return None

    media_png = generate_media_thumbnail(project, chosen, force=force, width=width)
    if not media_png or not os.path.isfile(media_png):
        return None

    os.makedirs(os.path.dirname(shot_png), exist_ok=True)
    tmp_png = f"{shot_png}.tmp.{os.getpid()}.png"
    try:
        shutil.copy2(media_png, tmp_png)
        os.replace(tmp_png, shot_png)
        shot.thumb = shot_png
        return shot_png
    except Exception:
        if os.path.isfile(tmp_png):
            try:
                os.remove(tmp_png)
            except OSError:
                pass
        return None


def set_shot_thumbnail_from_image(
    project: Project,
    shot: Shot,
    image_path: str,
    width: int = DEFAULT_THUMB_WIDTH,
) -> Optional[str]:
    """Imports and resizes any local image file as the shot's thumbnail."""
    if not project or not shot or not shot.shot_id or not image_path or not os.path.isfile(image_path):
        return None
    shot_png = get_shot_thumbnail_path(project, shot.shot_id)
    if not shot_png:
        return None
    ext = os.path.splitext(image_path)[1].lower()
    if extract_thumbnail_png(image_path, shot_png, width=width, is_movie=ext in MOVIE_EXTS):
        shot.thumb = shot_png
        return shot_png
    return None
