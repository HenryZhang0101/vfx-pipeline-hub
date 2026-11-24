import os
import re
import glob
import json
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

from config import (
    MEDIA_EXTENSIONS,
    PIPELINE_BIN_DIR,
    SEQUENCE_EXTENSIONS,
    get_ffmpeg_bin,
    get_ffprobe_bin,
    get_ocio_config,
    open_path_in_os,
)
from tools.base_tool import BaseToolPlugin


class DJVPlayerTool(BaseToolPlugin):
    """
    Modular DJV Review & Version Comparison Tool (DJV 3.4+ / 3.7.x with external FFmpeg CLI support).
    Supports single-click sequence/movie playback (EXR, DPX, H.264/ProRes MOV) and A/B version comparison
    (Wipe, Butterfly, Difference, Horizontal, Vertical, Tile, Overlay).
    """

    tool_id = "djv_player"
    display_name = "DJV Media Player & A/B Compare"
    category = "Review"
    description = "Open-source EXR/MOV review player with A/B version Wipe, Difference, and Side-by-Side comparison."
    requires_executable = True

    COMPARE_MODES = [
        "Wipe",
        "Difference",
        "Horizontal",
        "Vertical",
        "Tile",
        "Overlay",
        "A",
        "B",
    ]

    def auto_detect_executables(self) -> List[str]:
        found: List[str] = []

        def _add(path: str) -> None:
            norm = os.path.normpath(path)
            if os.path.isfile(norm) and norm not in found:
                found.append(norm)

        # 1. Portable DJV bundled inside bin/DJV/ (preferred for zero-install handoff)
        if os.path.isdir(PIPELINE_BIN_DIR):
            portable_roots = [os.path.join(PIPELINE_BIN_DIR, "DJV")]
            portable_roots.extend(sorted(glob.glob(os.path.join(PIPELINE_BIN_DIR, "DJV*")), reverse=True))
            for root in portable_roots:
                if not os.path.isdir(root):
                    continue
                if sys.platform == "win32":
                    for pattern in (
                        os.path.join(root, "bin", "djv.exe"),
                        os.path.join(root, "*", "bin", "djv.exe"),
                        os.path.join(root, "djv.exe"),
                    ):
                        for match in sorted(glob.glob(pattern), reverse=True):
                            _add(match)
                elif sys.platform == "darwin":
                    for pattern in (
                        os.path.join(root, "DJV*.app", "Contents", "MacOS", "DJV"),
                        os.path.join(root, "DJV*.app", "Contents", "Resources", "bin", "djv"),
                    ):
                        for match in sorted(glob.glob(pattern), reverse=True):
                            _add(match)

        # 2. System-installed DJV
        if sys.platform == "win32":
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            for d in sorted(glob.glob(os.path.join(program_files, "DJV*")), reverse=True):
                _add(os.path.join(d, "bin", "djv.exe"))
                _add(os.path.join(d, "djv.exe"))
        elif sys.platform == "darwin":
            for app_root in ("/Applications", os.path.expanduser("~/Applications")):
                for app_path in sorted(glob.glob(os.path.join(app_root, "DJV*.app")), reverse=True):
                    _add(os.path.join(app_path, "Contents", "MacOS", "DJV"))
                    _add(os.path.join(app_path, "Contents", "Resources", "bin", "djv"))

        which_djv = shutil.which("djv")
        if which_djv:
            _add(which_djv)
        return found

    def _build_djv_env(self, ocio_config: Optional[str] = None) -> Dict[str, str]:
        """Builds subprocess env with pipeline bin/ prepended and active OCIO config set."""
        env = os.environ.copy()
        if os.path.isdir(PIPELINE_BIN_DIR):
            env["PATH"] = PIPELINE_BIN_DIR + os.pathsep + env.get("PATH", "")
        resolved_ocio = get_ocio_config(ocio_config or "")
        if resolved_ocio and os.path.isfile(resolved_ocio):
            env["OCIO"] = os.path.normpath(resolved_ocio)
        return env

    def _ensure_djv_ffmpeg_settings(self) -> None:
        """
        Ensures DJV 3.4+ external FFmpegCmd paths point to the pipeline's FFmpeg/FFprobe
        so H.264 and ProRes .mov files decode out of the box.
        """
        ffmpeg_bin = get_ffmpeg_bin()
        ffprobe_bin = get_ffprobe_bin()
        if not (os.path.isfile(ffmpeg_bin) and os.path.isfile(ffprobe_bin)):
            return

        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "")
            candidates = [os.path.join(appdata, "DJV", "djv.3.json")] if appdata else []
        elif sys.platform == "darwin":
            candidates = [
                os.path.expanduser("~/Library/Application Support/DJV/djv.3.json"),
                os.path.expanduser("~/Documents/DJV/djv.3.json"),
            ]
        else:
            candidates = [os.path.expanduser("~/.config/DJV/djv.3.json")]

        for cfg_path in candidates:
            if not cfg_path:
                continue
            try:
                data: dict = {}
                if os.path.isfile(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
                ffmpeg_cmd = data.get("FFmpegCmd")
                if not isinstance(ffmpeg_cmd, dict):
                    ffmpeg_cmd = {}
                norm_ffmpeg = os.path.normpath(ffmpeg_bin)
                norm_ffprobe = os.path.normpath(ffprobe_bin)
                if ffmpeg_cmd.get("FFmpeg") != norm_ffmpeg or ffmpeg_cmd.get("FFprobe") != norm_ffprobe:
                    ffmpeg_cmd["FFmpeg"] = norm_ffmpeg
                    ffmpeg_cmd["FFprobe"] = norm_ffprobe
                    data["FFmpegCmd"] = ffmpeg_cmd
                    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4)
                break
            except Exception:
                pass

    def resolve_playable_target(self, path_or_folder: str) -> str:
        """
        Resolves a folder path, printf pattern (%04d), or hash pattern (####)
        into the concrete first frame file path so DJV loads the full sequence cleanly.
        """
        if not path_or_folder:
            return ""
        target = os.path.normpath(path_or_folder)

        if os.path.isfile(target):
            return target

        # If pattern like .../R01-0010_comp_v0001.%04d.exr or .####.exr, inspect parent directory
        parent_dir = target if os.path.isdir(target) else os.path.dirname(target)
        if not os.path.isdir(parent_dir):
            return ""

        files = sorted([
            f for f in os.listdir(parent_dir)
            if os.path.isfile(os.path.join(parent_dir, f))
            and os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS
        ])
        if not files:
            # Check one level deeper (e.g. AOV beauty subfolder)
            for sub in sorted(os.listdir(parent_dir)):
                sub_p = os.path.join(parent_dir, sub)
                if os.path.isdir(sub_p):
                    sub_files = sorted([
                        f for f in os.listdir(sub_p)
                        if os.path.isfile(os.path.join(sub_p, f))
                        and os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS
                    ])
                    if sub_files:
                        return os.path.normpath(os.path.join(sub_p, sub_files[0]))
            return ""

        return os.path.normpath(os.path.join(parent_dir, files[0]))

    def play_media(
        self,
        media_path: str,
        playback: str = "Forward",
        loop: str = "Loop",
        speed: Optional[float] = None,
        ocio_config: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Launches DJV to play a single image sequence or video file, falling back to OS default player if DJV is unavailable."""
        return self.play_media_list(
            [media_path],
            playback=playback,
            loop=loop,
            speed=speed,
            ocio_config=ocio_config,
        )

    def play_media_list(
        self,
        media_paths: List[str],
        playback: str = "Forward",
        loop: str = "Loop",
        speed: Optional[float] = None,
        ocio_config: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Launches DJV with one or more image sequences/video files loaded together."""
        resolved_list: List[str] = []
        for p in media_paths:
            res = self.resolve_playable_target(p)
            if res and os.path.exists(res) and res not in resolved_list:
                resolved_list.append(res)

        if not resolved_list:
            return False, "No playable media files found in selection."

        exe = self.get_executable()
        if exe and os.path.isfile(exe):
            self._ensure_djv_ffmpeg_settings()
            cmd = [exe, *resolved_list, "-playback", playback, "-loop", loop, "-hideSetup"]
            if speed and speed > 0:
                cmd.extend(["-speed", str(speed)])
            resolved_ocio = get_ocio_config(ocio_config or "")
            if resolved_ocio and os.path.isfile(resolved_ocio):
                cmd.extend(["-ocio", os.path.normpath(resolved_ocio)])

            try:
                subprocess.Popen(cmd, cwd=os.path.dirname(resolved_list[0]), env=self._build_djv_env(resolved_ocio))
                if len(resolved_list) == 1:
                    return True, f"Playing in DJV: {os.path.basename(resolved_list[0])}"
                return True, f"Loaded {len(resolved_list)} clips in DJV"
            except Exception:
                pass

        try:
            open_path_in_os(resolved_list[0])
            return True, f"DJV unavailable — opened in OS default player: {os.path.basename(resolved_list[0])}"
        except Exception as e:
            return False, f"Failed to open media in DJV or OS default player: {e}"

    def compare_media(
        self,
        media_a_path: str,
        media_b_path: str,
        compare_mode: str = "Wipe",
        playback: str = "Forward",
        loop: str = "Loop",
        speed: Optional[float] = None,
        ocio_config: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Launches DJV comparing Media A and Media B using the chosen compare mode:
        "Wipe", "Difference", "Horizontal", "Vertical", "Tile", "Overlay", "A", "B".
        """
        exe = self.get_executable()
        if not exe or not os.path.isfile(exe):
            return False, "DJV executable not found. Please verify DJV path in Module Settings."

        res_a = self.resolve_playable_target(media_a_path)
        res_b = self.resolve_playable_target(media_b_path)
        if not res_a or not os.path.exists(res_a):
            return False, f"Version A media not found: {media_a_path}"
        if not res_b or not os.path.exists(res_b):
            return False, f"Version B media not found: {media_b_path}"

        self._ensure_djv_ffmpeg_settings()
        mode = compare_mode if compare_mode in self.COMPARE_MODES else "Wipe"
        cmd = [
            exe,
            res_a,
            "-compare",
            res_b,
            "-compareMode",
            mode,
            "-playback",
            playback,
            "-loop",
            loop,
            "-hideSetup",
        ]
        if speed and speed > 0:
            cmd.extend(["-speed", str(speed)])
        resolved_ocio = get_ocio_config(ocio_config or "")
        if resolved_ocio and os.path.isfile(resolved_ocio):
            cmd.extend(["-ocio", os.path.normpath(resolved_ocio)])

        try:
            subprocess.Popen(cmd, cwd=os.path.dirname(res_a), env=self._build_djv_env(resolved_ocio))
            return True, f"DJV Compare ({mode}): {os.path.basename(res_a)} vs {os.path.basename(res_b)}"
        except Exception as e:
            return False, f"Failed to launch DJV Compare: {e}"
