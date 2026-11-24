import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple, Any, Callable

from config import (
    MEDIA_EXTENSIONS,
    SEQUENCE_EXTENSIONS,
    ASSET_3D_EXTENSIONS,
    DEFAULT_START_FRAME,
    get_ffprobe_bin,
)
from core.timecode import format_shot_id, get_png_dimensions
from tools.base_tool import BaseToolPlugin


class MediaIngesterTool(BaseToolPlugin):
    """
    Modular Standalone Media, Plate & 3D Asset Ingestion Tool.
    Ingests image sequences (.exr, .dpx, .png, .tif), video files (.mov, .mp4),
    or 3D assets/caches (.abc, .usd, .fbx, .obj, .vdb) directly into a shot
    without requiring DaVinci Resolve or PrismCore.
    """

    tool_id = "media_ingester"
    display_name = "Media & Plate Ingester"
    category = "Ingestion"
    description = "Ingest plates, image sequences, reference movies, or 3D caches into shots with Copy or Zero-Space Hardlink."
    requires_executable = False

    CATEGORIES = [
        ("media", "Media (Plates, Renders, Playblasts, Ref)"),
        ("assets", "3D Assets & Caches (Cam, Geo, USD, VDB, LUT)"),
    ]

    COMMON_IDENTIFIERS = {
        "media": ["plate", "bg", "fg", "cleanplate", "comp", "lgt", "fx", "playblast", "ref"],
        "assets": ["cam", "geo", "abc", "usd", "fbx", "vdb", "lut"],
    }

    INGEST_MODES = [
        ("copy_rename", "Copy & Rename to Pipeline Convention"),
        ("hardlink", "Hardlink & Rename (Instant, 0 Extra Disk Space)"),
        ("copy_original", "Copy (Keep Original Filenames)"),
    ]

    @staticmethod
    def _parse_fps(rate_str: str) -> float:
        if not rate_str or rate_str == "0/0":
            return 0.0
        if "/" in rate_str:
            parts = rate_str.split("/", 1)
            try:
                num, den = float(parts[0]), float(parts[1])
                return round(num / den, 3) if den > 0 else 0.0
            except ValueError:
                return 0.0
        try:
            return round(float(rate_str), 3)
        except ValueError:
            return 0.0

    def _probe_media_metadata(self, file_path: str, is_sequence: bool = False) -> Dict[str, Any]:
        """Probe width, height, fps, frame_count, and duration via ffprobe (with PNG header fallback)."""
        meta: Dict[str, Any] = {
            "width": 0,
            "height": 0,
            "resolution": "",
            "fps": 0.0,
            "duration_sec": 0.0,
            "probed_frames": 0,
        }
        if not file_path or not os.path.isfile(file_path):
            return meta

        ext = os.path.splitext(file_path)[1].lower()
        if ext in ASSET_3D_EXTENSIONS:
            return meta

        ffprobe_bin = get_ffprobe_bin()
        ffprobe_exe = ffprobe_bin if os.path.isfile(ffprobe_bin) else (shutil.which("ffprobe") or "")
        if ffprobe_exe:
            cmd = [
                ffprobe_exe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                file_path,
            ]
            kwargs: Dict[str, Any] = {"capture_output": True, "text": True, "timeout": 8}
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                res = subprocess.run(cmd, **kwargs)
                if res.returncode == 0 and res.stdout:
                    data = json.loads(res.stdout)
                    streams = data.get("streams") or []
                    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
                    if vstream:
                        w = int(vstream.get("width") or 0)
                        h = int(vstream.get("height") or 0)
                        if w > 0 and h > 0:
                            meta["width"] = w
                            meta["height"] = h
                            meta["resolution"] = f"{w}x{h}"

                        if not is_sequence:
                            fps = self._parse_fps(vstream.get("r_frame_rate") or "") or self._parse_fps(
                                vstream.get("avg_frame_rate") or ""
                            )
                            if fps > 0:
                                meta["fps"] = fps
                            dur_raw = vstream.get("duration") or (data.get("format") or {}).get("duration")
                            if dur_raw:
                                try:
                                    meta["duration_sec"] = round(float(dur_raw), 2)
                                except (TypeError, ValueError):
                                    pass
                            nb_frames = vstream.get("nb_frames")
                            if nb_frames and str(nb_frames).isdigit():
                                meta["probed_frames"] = int(nb_frames)
                            elif meta["duration_sec"] > 0 and meta["fps"] > 0:
                                meta["probed_frames"] = max(1, int(round(meta["duration_sec"] * meta["fps"])))
            except Exception:
                pass

        if meta["width"] == 0 and ext == ".png":
            w, h = get_png_dimensions(file_path)
            if w > 0 and h > 0:
                meta["width"] = w
                meta["height"] = h
                meta["resolution"] = f"{w}x{h}"

        return meta

    def analyze_source(self, source_path: str) -> Dict[str, Any]:
        """
        Inspects a selected file or directory.
        If a single frame of an image sequence is selected (e.g. shot.0001.exr),
        automatically discovers the entire matching frame sequence in that directory!
        """
        if not source_path or not os.path.exists(source_path):
            return {"valid": False, "error": "Source path does not exist."}

        source_path = os.path.normpath(source_path)

        # If user selected a single file
        if os.path.isfile(source_path):
            parent_dir = os.path.dirname(source_path)
            fname = os.path.basename(source_path)
            ext = os.path.splitext(fname)[1].lower()

            m = re.match(r"^(.*?)([._])(\d{3,6})\.([a-zA-Z0-9]+)$", fname)
            if m and ext in SEQUENCE_EXTENSIONS:
                base, sep, frame_str, ext_no_dot = m.groups()
                # Scan sibling files in parent_dir matching this sequence
                seq_files: List[Tuple[int, str]] = []
                pat = re.compile(
                    rf"^{re.escape(base)}{re.escape(sep)}(\d{{{len(frame_str)},\d*}})\.{re.escape(ext_no_dot)}$",
                    re.IGNORECASE,
                )
                for sib in sorted(os.listdir(parent_dir)):
                    sib_full = os.path.join(parent_dir, sib)
                    if not os.path.isfile(sib_full):
                        continue
                    ms = pat.match(sib)
                    if ms:
                        seq_files.append((int(ms.group(1)), sib_full))

                if seq_files:
                    seq_files.sort(key=lambda x: x[0])
                    frames = [x[0] for x in seq_files]
                    files = [x[1] for x in seq_files]
                    padding = len(frame_str)
                    meta = self._probe_media_metadata(files[0], is_sequence=True)
                    return {
                        "valid": True,
                        "is_sequence": True,
                        "source_dir": parent_dir,
                        "display_name": f"{base}{sep}{'#' * padding}.{ext_no_dot}",
                        "files": files,
                        "frames": frames,
                        "start_frame": frames[0],
                        "end_frame": frames[-1],
                        "frame_count": len(frames),
                        "padding": padding,
                        "ext": f".{ext_no_dot.lower()}",
                        "suggested_category": "media",
                        **meta,
                    }

            # Single movie or 3D asset file
            suggested_cat = "assets" if ext in ASSET_3D_EXTENSIONS else "media"
            meta = self._probe_media_metadata(source_path, is_sequence=False)
            probed_frames = meta.get("probed_frames", 0)
            frame_count = probed_frames if probed_frames > 0 else 1
            return {
                "valid": True,
                "is_sequence": False,
                "source_dir": parent_dir,
                "display_name": fname,
                "files": [source_path],
                "frames": [DEFAULT_START_FRAME],
                "start_frame": DEFAULT_START_FRAME,
                "end_frame": DEFAULT_START_FRAME + frame_count - 1,
                "frame_count": frame_count,
                "padding": 4,
                "ext": ext,
                "suggested_category": suggested_cat,
                **meta,
            }

        # If user selected a directory
        if os.path.isdir(source_path):
            valid_exts = set(MEDIA_EXTENSIONS) | set(ASSET_3D_EXTENSIONS)
            dir_files = sorted([
                f for f in os.listdir(source_path)
                if os.path.isfile(os.path.join(source_path, f))
                and (os.path.splitext(f)[1].lower() in valid_exts or f.lower().endswith(".bgeo.sc"))
            ])
            if not dir_files:
                return {"valid": False, "error": "No supported media or 3D asset files found in directory."}

            # Pick first file and analyze
            return self.analyze_source(os.path.join(source_path, dir_files[0]))

        return {"valid": False, "error": "Unsupported source path."}

    def ingest_to_shot(
        self,
        vfx_root: str,
        seq: str,
        shot: str,
        source_info: Dict[str, Any],
        category: str = "media",
        identifier: str = "plate",
        version: str = "v0001",
        mode: str = "copy_rename",
        renumber_to_start_frame: bool = True,
        target_start_frame: int = DEFAULT_START_FRAME,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[bool, str, str]:
        """
        Executes media/asset ingestion into the target shot folder.
        Returns (success, message, target_version_dir).
        """
        if not source_info.get("valid"):
            return False, source_info.get("error", "Invalid source media."), ""

        seq = str(seq or "").strip()
        shot = str(shot or "").strip()
        identifier = str(identifier or "plate").strip()
        version = str(version or "v0001").strip()
        if not version.startswith("v"):
            try:
                version = f"v{int(version):04d}"
            except ValueError:
                version = f"v{version}"

        cat_folder = "assets" if category == "assets" else "media"
        target_dir = os.path.join(vfx_root, seq, shot, cat_folder, identifier, version)

        try:
            os.makedirs(target_dir, exist_ok=True)
            files: List[str] = source_info.get("files", [])
            frames: List[int] = source_info.get("frames", [])
            is_seq: bool = source_info.get("is_sequence", False)
            ext: str = source_info.get("ext", ".exr")
            total = len(files)

            norm_id = format_shot_id(seq, shot)
            for idx, src_file in enumerate(files):
                if not os.path.isfile(src_file):
                    continue

                if mode == "copy_original":
                    dst_name = os.path.basename(src_file)
                else:
                    if is_seq:
                        if renumber_to_start_frame:
                            frame_num = target_start_frame + idx
                        else:
                            frame_num = frames[idx] if idx < len(frames) else (target_start_frame + idx)
                        dst_name = f"{norm_id}_{identifier}_{version}.{frame_num:04d}{ext}"
                    else:
                        dst_name = f"{norm_id}_{identifier}_{version}{ext}"

                dst_path = os.path.join(target_dir, dst_name)
                if os.path.exists(dst_path):
                    os.remove(dst_path)

                if mode == "hardlink":
                    try:
                        os.link(src_file, dst_path)
                    except OSError:
                        # Cross-drive fallback to copy2
                        shutil.copy2(src_file, dst_path)
                else:
                    shutil.copy2(src_file, dst_path)

                if progress_callback and (idx % 5 == 0 or idx == total - 1):
                    progress_callback(idx + 1, total, dst_name)

            method_label = "Hardlinked" if mode == "hardlink" else "Copied"
            return (
                True,
                f"{method_label} {total} file(s) into {norm_id} [{category}/{identifier}/{version}]",
                os.path.normpath(target_dir),
            )
        except Exception as e:
            return False, f"Ingestion error: {e}", target_dir
