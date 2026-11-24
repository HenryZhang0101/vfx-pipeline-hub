import datetime
import fractions
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from config import (
    DEFAULT_EXPORT_FOLDER,
    DEFAULT_SLATE_DISCLAIMER,
    FOLDER_EXPORTS,
    PIPELINE_ASSETS_DIR,
    SEQUENCE_EXTENSIONS,
    get_ffmpeg_bin,
    get_ffprobe_bin,
    get_slate_logo_path,
    load_user_settings,
)
from tools.base_tool import BaseToolPlugin


# ---------------------------------------------------------------------------
# Color & Typography Constants (Exact 1080p calibration from vfx_slate/renderer.py
# matching Xin_Studio_VFX_Review_Effect_v7.2_Logo.setting)
# ---------------------------------------------------------------------------

# 11-step neutral grayscale patches calibrated to Fusion display output
GRAY_BARS: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.090909, (12, 12, 12)),
    (0.172727, (28, 28, 28)),
    (0.254545, (48, 48, 48)),
    (0.336364, (72, 72, 72)),
    (0.418182, (100, 100, 100)),
    (0.500000, (132, 132, 132)),
    (0.581818, (166, 166, 166)),
    (0.663636, (198, 198, 198)),
    (0.745455, (224, 224, 224)),
    (0.827273, (242, 242, 242)),
    (0.909091, (254, 254, 254)),
]

# 7-step SMPTE/QC color reference bars calibrated to Fusion display output
COLOR_BARS: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.114286, (235, 235, 235)),  # White / Gray
    (0.242857, (243, 248, 0)),    # Yellow
    (0.371429, (0, 238, 235)),    # Cyan
    (0.500000, (0, 245, 0)),      # Green
    (0.628571, (248, 0, 245)),    # Magenta
    (0.757143, (248, 8, 18)),     # Red
    (0.885714, (12, 8, 246)),     # Blue
]


def _subprocess_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _float_rgb(r: float, g: float, b: float) -> Tuple[int, int, int]:
    return (
        max(0, min(255, int(round(r * 255)))),
        max(0, min(255, int(round(g * 255)))),
        max(0, min(255, int(round(b * 255)))),
    )


def _norm_to_px(nx: float, ny: float, width: int, height: int) -> Tuple[int, int]:
    """Convert Fusion bottom-left normalized (0..1) coords to Pillow top-left pixel coords."""
    px = int(round(nx * width))
    py = int(round((1.0 - ny) * height))
    return px, py


def _rect_from_center(
    cx: float, cy: float, w_norm: float, h_norm: float, width: int, height: int
) -> Tuple[int, int, int, int]:
    """Convert Fusion RectangleMask (Center, Width, Height) to Pillow (x0, y0, x1, y1)."""
    x0 = int(round((cx - w_norm / 2.0) * width))
    x1 = int(round((cx + w_norm / 2.0) * width))
    y_center = (1.0 - cy) * height
    half_h = (h_norm * height) / 2.0
    y0 = int(round(y_center - half_h))
    y1 = max(y0 + 1, int(round(y_center + half_h)))
    return (x0, y0, x1, y1)


def _get_bold_font_path() -> Optional[str]:
    """Resolve Bold grotesque font matching 'Helvetica Neue Bold' across Windows & macOS."""
    fonts_dir = os.path.join(PIPELINE_ASSETS_DIR, "fonts")
    if os.path.isdir(fonts_dir):
        for fn in sorted(os.listdir(fonts_dir)):
            if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                return os.path.join(fonts_dir, fn)

    if sys.platform == "win32":
        win_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for cand in ("HelveticaNeue-Bold.ttf", "arialbd.ttf", "segoeuib.ttf", "calibrib.ttf"):
            p = os.path.join(win_fonts, cand)
            if os.path.isfile(p):
                return p
    elif sys.platform == "darwin":
        for cand in (
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ):
            if os.path.isfile(cand):
                return cand
    else:
        for cand in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ):
            if os.path.isfile(cand):
                return cand
    return None


def _load_font(size_px: int):
    font_path = _get_bold_font_path()
    size_px = max(10, int(size_px))
    if font_path:
        try:
            if font_path.lower().endswith(".ttc"):
                try:
                    return ImageFont.truetype(font_path, size=size_px, index=1)
                except Exception:
                    return ImageFont.truetype(font_path, size=size_px, index=0)
            return ImageFont.truetype(font_path, size=size_px)
        except Exception:
            pass
    return ImageFont.load_default()


def frame_to_smpte_timecode(frame_num: int, fps_float: float) -> str:
    """Converts a frame number (e.g. 1000 or 1001) to non-drop SMPTE timecode HH:MM:SS:FF."""
    timebase = max(1, int(round(fps_float)))
    total_frames = max(0, int(frame_num))
    ff = total_frames % timebase
    total_seconds = total_frames // timebase
    ss = total_seconds % 60
    total_minutes = total_seconds // 60
    mm = total_minutes % 60
    hh = total_minutes // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def render_slate_image(
    first_frame_image_path: str,
    output_slate_png: str,
    metadata: Dict[str, str],
    width: int = 1920,
    height: int = 1080,
    logo_path: Optional[str] = None,
    studio_name: str = "",
    disclaimer: Optional[str] = None,
) -> str:
    r"""
    Render the 1-frame VFX Review Slate card (Frame 1000) matching the calibrated
    1080p normalized Fusion slate layout.
    """
    W = int(width)
    H = int(height)

    if first_frame_image_path and os.path.isfile(first_frame_image_path):
        source_frame = Image.open(first_frame_image_path).convert("RGB")
        if source_frame.size != (W, H):
            source_frame = source_frame.resize((W, H), Image.Resampling.LANCZOS)
    else:
        source_frame = Image.new("RGB", (W, H), (28, 30, 34))

    # 1. BlurBG (XBlurSize=24, YBlurSize=24) & DarkBG (Gain=0.82, Saturation=0.88)
    blur_radius = max(8.0, (W / 1920.0) * 28.0)
    bg = source_frame.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    bg = ImageEnhance.Color(bg).enhance(0.88)
    bg = ImageEnhance.Brightness(bg).enhance(0.82)

    canvas = bg.convert("RGBA")

    # 2. Thumb (Center={0.26, 0.505}, Size=0.42)
    thumb_w = int(round(W * 0.42))
    thumb_h = int(round(H * 0.42))
    thumb_img = source_frame.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS).convert("RGBA")
    thumb_cx, thumb_cy = _norm_to_px(0.26, 0.505, W, H)
    thumb_x0 = thumb_cx - thumb_w // 2
    thumb_y0 = thumb_cy - thumb_h // 2
    canvas.paste(thumb_img, (thumb_x0, thumb_y0))

    draw = ImageDraw.Draw(canvas)

    # 3. QC Reference Bars: 11 Neutral Grayscale Patches (Y=0.255, W=0.082418, H=0.026)
    for cx, rgb_gray in GRAY_BARS:
        box = _rect_from_center(cx, 0.255, 0.082418, 0.026, W, H)
        draw.rectangle(box, fill=rgb_gray)

    # 4. QC Reference Bars: 7 Color Reference Bars (Y=0.205, W=0.129171, H=0.026)
    for cx, rgb_col in COLOR_BARS:
        box = _rect_from_center(cx, 0.205, 0.129171, 0.026, W, H)
        draw.rectangle(box, fill=rgb_col)

    # 5. Divider Line (Center={0.5, 0.165}, Width=0.90, Height=0.0018, RGB=0.92)
    div_box = _rect_from_center(0.5, 0.165, 0.90, 0.0018, W, H)
    draw.rectangle(div_box, fill=_float_rgb(0.92, 0.92, 0.92))

    # 6. Typography Helper
    # In Fusion TextPlus, Size is normalized to image WIDTH (W), where W * fusion_size * 0.686
    # (= H * fusion_size * 1.22 on 16:9) produces the exact cap-height of Fusion's 1080p render.
    def draw_fusion_text(
        text: str,
        nx: float,
        ny: float,
        fusion_size: float,
        rgb_float: Tuple[float, float, float],
        left_aligned: bool = False,
    ) -> None:
        px, py = _norm_to_px(nx, ny, W, H)
        font_px = max(12, int(round(W * fusion_size * 0.686)))
        font = _load_font(font_px)
        color = _float_rgb(*rgb_float)
        anchor = "lm" if left_aligned else "mm"
        draw.text((px, py), str(text), font=font, fill=color, anchor=anchor)

    # Header: "VFX REVIEW" (Size=0.052, Center={0.155, 0.855}, RGB=0.96)
    draw_fusion_text("VFX REVIEW", 0.155, 0.855, 0.052, (0.96, 0.96, 0.96), left_aligned=False)

    # Right Metadata Column (Left-aligned at X=0.565, matching HorizontalLeftCenterRight=-1)
    label_rgb = (0.72, 0.72, 0.72)
    value_rgb = (0.97, 0.97, 0.97)

    # SHOT ID
    draw_fusion_text("SHOT ID", 0.565, 0.705, 0.022, label_rgb, left_aligned=True)
    draw_fusion_text(metadata.get("shot_id", "SHOT_0010"), 0.565, 0.665, 0.027, value_rgb, left_aligned=True)

    # VERSION
    draw_fusion_text("VERSION", 0.565, 0.585, 0.022, label_rgb, left_aligned=True)
    draw_fusion_text(metadata.get("version", "V001"), 0.565, 0.545, 0.027, value_rgb, left_aligned=True)

    # DATE
    draw_fusion_text("DATE", 0.565, 0.465, 0.022, label_rgb, left_aligned=True)
    draw_fusion_text(metadata.get("date", "----"), 0.565, 0.425, 0.027, value_rgb, left_aligned=True)

    # FRAMES
    draw_fusion_text("FRAMES", 0.565, 0.345, 0.022, label_rgb, left_aligned=True)
    draw_fusion_text(metadata.get("frames", "1001 - 1001 (1)"), 0.565, 0.305, 0.027, value_rgb, left_aligned=True)

    # Bottom Technical Row (Centered at respective X coords)
    bot_label_rgb = (0.62, 0.62, 0.62)
    bot_value_rgb = (0.92, 0.92, 0.92)

    # RESOLUTION
    draw_fusion_text("RESOLUTION", 0.089, 0.125, 0.0185, bot_label_rgb, left_aligned=False)
    draw_fusion_text(metadata.get("resolution", f"{W}x{H}"), 0.089, 0.088, 0.0215, bot_value_rgb, left_aligned=False)

    # FRAME RATE
    draw_fusion_text("FRAME RATE", 0.255, 0.125, 0.0185, bot_label_rgb, left_aligned=False)
    draw_fusion_text(metadata.get("fps", "24 fps"), 0.255, 0.088, 0.0215, bot_value_rgb, left_aligned=False)

    # COLOR SPACE / GAMMA
    draw_fusion_text("COLOR SPACE / GAMMA", 0.425, 0.125, 0.0185, bot_label_rgb, left_aligned=False)
    draw_fusion_text(metadata.get("color_space", "Rec.709 / 2.4"), 0.425, 0.088, 0.0215, bot_value_rgb, left_aligned=False)

    # Footer Disclaimer
    footer_text = DEFAULT_SLATE_DISCLAIMER if disclaimer is None else disclaimer.strip()
    if footer_text:
        draw_fusion_text(
            footer_text,
            0.705,
            0.125,
            0.015,
            (0.68, 0.68, 0.68),
            left_aligned=False,
        )

    # 7. Studio Logo Overlay (1080p Fusion Loader calibration: width ~0.266*W, Center={0.805, 0.865})
    if logo_path is None:
        logo_path = get_slate_logo_path()

    if logo_path and os.path.isfile(logo_path):
        try:
            logo_img = Image.open(logo_path).convert("RGBA")
            target_logo_w = max(16, int(round(W * 0.266)))
            aspect = logo_img.height / float(max(1, logo_img.width))
            target_logo_h = max(16, int(round(target_logo_w * aspect)))
            logo_resized = logo_img.resize((target_logo_w, target_logo_h), Image.Resampling.LANCZOS)
            logo_cx, logo_cy = _norm_to_px(0.805, 0.865, W, H)
            lx = logo_cx - target_logo_w // 2
            ly = logo_cy - target_logo_h // 2
            canvas.alpha_composite(logo_resized, dest=(lx, ly))
        except Exception:
            pass
    elif (studio_name or "").strip():
        draw_fusion_text(
            studio_name.strip().upper(),
            0.805,
            0.865,
            0.032,
            (0.95, 0.95, 0.95),
            left_aligned=False,
        )

    final_rgb = canvas.convert("RGB")
    if output_slate_png:
        os.makedirs(os.path.dirname(os.path.abspath(output_slate_png)), exist_ok=True)
        final_rgb.save(output_slate_png, format="PNG")
    return output_slate_png


class SlateDeliveryTool(BaseToolPlugin):
    r"""
    Modular Pipeline Tool for VFX Review Slates (Frame 1000 + H.264) and
    Final Client Deliveries (Apple ProRes 4444), powered by bundled FFmpeg (`h:\_pipeline\bin\ffmpeg.exe`).
    Supports both Movie files (.mov/.mp4/.mxf) and Image Sequences (.exr/.dpx/.png/.tif).
    """

    tool_id = "slate_delivery"
    display_name = "VFX Slate, Review QT & ProRes 4444 Delivery"
    category = "Review & Delivery"
    description = (
        "Generates Frame 1000 VFX Review Slates + H.264 QuickTimes and ProRes 4444 Deliveries "
        "using bundled FFmpeg, with multi-clip batch rename support."
    )
    requires_executable = True

    def auto_detect_executables(self) -> List[str]:
        found = []
        ffmpeg_bin = get_ffmpeg_bin()
        if os.path.isfile(ffmpeg_bin):
            found.append(ffmpeg_bin)
        which_ff = shutil.which("ffmpeg")
        if which_ff and os.path.isfile(which_ff) and os.path.normpath(which_ff) not in [os.path.normpath(p) for p in found]:
            found.append(which_ff)
        return found

    def get_ffmpeg(self) -> str:
        exe = self.get_executable()
        if exe and os.path.isfile(exe):
            return exe
        ffmpeg_bin = get_ffmpeg_bin()
        if os.path.isfile(ffmpeg_bin):
            return ffmpeg_bin
        return shutil.which("ffmpeg") or "ffmpeg"

    def get_ffprobe(self) -> str:
        ffprobe_bin = get_ffprobe_bin()
        if os.path.isfile(ffprobe_bin):
            return ffprobe_bin
        ffmpeg_exe = self.get_ffmpeg()
        if ffmpeg_exe:
            cand = os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe.exe" if sys.platform == "win32" else "ffprobe")
            if os.path.isfile(cand):
                return cand
        return shutil.which("ffprobe") or "ffprobe"

    # ------------------------------------------------------------------
    # Media & Image Sequence Inspection
    # ------------------------------------------------------------------
    def inspect_clip_source(
        self,
        source_path: str,
        shot_id: str = "",
        identifier: str = "",
        version: str = "",
        shot_info: Optional[Dict] = None,
    ) -> Dict:
        r"""
        Inspects either a movie file (.mov/.mp4) OR an image sequence folder/file (.exr/.png/.dpx)
        and returns a unified clip descriptor dictionary ready for `BatchQTExportDialog`.
        """
        shot_info = shot_info or {}
        norm_path = os.path.normpath(source_path)

        is_sequence = False
        seq_pattern = ""
        first_frame_file = norm_path
        start_frame = int(shot_info.get("startFrame", 1001) or 1001)
        end_frame = int(shot_info.get("endFrame", 1001) or 1001)
        frame_count = 1
        ext = os.path.splitext(norm_path)[1].lower()

        # Check if directory or sequence frame file
        if os.path.isdir(norm_path):
            seq_info = self._scan_sequence_directory(norm_path)
            if seq_info:
                is_sequence = True
                seq_pattern = seq_info["pattern_path"]
                first_frame_file = seq_info["first_file"]
                start_frame = seq_info["start_frame"]
                end_frame = seq_info["end_frame"]
                frame_count = seq_info["frame_count"]
                ext = seq_info["ext"]
            else:
                # Directory containing a movie file (.mov / .mp4 / .mxf)
                mov_files = sorted(
                    f for f in os.listdir(norm_path)
                    if os.path.splitext(f)[1].lower() in (".mov", ".mp4", ".mxf") and not f.startswith(".")
                )
                if mov_files:
                    norm_path = os.path.join(norm_path, mov_files[0])
                    first_frame_file = norm_path
                    ext = os.path.splitext(norm_path)[1].lower()
        elif os.path.isfile(norm_path) and ext in SEQUENCE_EXTENSIONS:
            parent_dir = os.path.dirname(norm_path)
            seq_info = self._scan_sequence_directory(parent_dir, target_file=norm_path)
            if seq_info:
                is_sequence = True
                seq_pattern = seq_info["pattern_path"]
                first_frame_file = seq_info["first_file"]
                start_frame = seq_info["start_frame"]
                end_frame = seq_info["end_frame"]
                frame_count = seq_info["frame_count"]
                ext = seq_info["ext"]

        # Probe dimensions, fps, audio
        width = 1920
        height = 1080
        fps_float = float(shot_info.get("fps", 24.0) or 24.0)
        fps_rational = f"{int(round(fps_float))}/1" if abs(fps_float - round(fps_float)) < 0.01 else f"{fps_float:.3f}"
        fps_display = f"{int(round(fps_float))} fps" if abs(fps_float - round(fps_float)) < 0.01 else f"{fps_float:.2f} fps"
        has_audio = False
        detected_color = "ACEScg / Linear" if ext == ".exr" else "Rec.709 / 2.4"

        probe_target = first_frame_file if is_sequence else norm_path
        if os.path.isfile(probe_target):
            probed = self._probe_file(probe_target, is_sequence=is_sequence)
            width = probed.get("width", width)
            height = probed.get("height", height)
            if not is_sequence:
                fps_float = probed.get("fps_float", fps_float)
                fps_rational = probed.get("fps_rational", fps_rational)
                fps_display = probed.get("fps_display", fps_display)
                frame_count = probed.get("nb_frames", frame_count)
                end_frame = start_frame + max(1, frame_count) - 1
                has_audio = probed.get("has_audio", False)
                if probed.get("color_space") and probed["color_space"] != "Unknown":
                    detected_color = probed["color_space"]

        # Parse shot_id, version, identifier from filename if not explicitly passed
        parsed = self._parse_filename_tokens(first_frame_file if is_sequence else norm_path)
        final_shot_id = shot_id or parsed.get("shot_id") or "SHOT_0010"
        final_ver_raw = version or parsed.get("version") or "v0001"
        # Format slate version display (e.g. V002) while keeping pipeline stem (e.g. v0002)
        ver_digits = re.sub(r"[^0-9]", "", final_ver_raw)
        slate_version = f"V{int(ver_digits):03d}" if ver_digits else final_ver_raw.upper()
        stem_version = f"v{int(ver_digits):04d}" if ver_digits else final_ver_raw

        final_ident = identifier or parsed.get("identifier") or "comp"
        date_str = parsed.get("date") or datetime.date.today().strftime("%Y-%m-%d")

        # Default clean output filename stem (without extension)
        default_output_name = f"{final_shot_id}_{final_ident}_{stem_version}"

        return {
            "source_path": norm_path,
            "is_sequence": is_sequence,
            "sequence_pattern": seq_pattern,
            "first_frame_file": first_frame_file,
            "ext": ext,
            "shot_id": final_shot_id,
            "identifier": final_ident,
            "version": slate_version,
            "stem_version": stem_version,
            "output_name": default_output_name,
            "date": date_str,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "frame_count": frame_count,
            "frames_display": f"{start_frame} - {end_frame} ({frame_count})",
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}",
            "fps_float": fps_float,
            "fps_rational": fps_rational,
            "fps_display": fps_display,
            "color_space": detected_color,
            "has_audio": has_audio,
        }

    def _scan_sequence_directory(self, folder: str, target_file: str = "") -> Optional[Dict]:
        """Detects frame sequence pattern (`prefix.%04d.ext`), start_frame, end_frame, and count."""
        if not os.path.isdir(folder):
            return None

        files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in SEQUENCE_EXTENSIONS and not f.startswith(".")
        )
        if not files:
            return None

        # Match trailing frame number before extension: e.g. R01-0010_comp_v0002.1001.exr or name_1001.png
        frame_re = re.compile(r"^(?P<prefix>.*?)(?P<sep>[._])(?P<frame>\d{3,6})(?P<ext>\.[^.]+)$", re.IGNORECASE)

        groups: Dict[Tuple[str, str, int, str], List[Tuple[int, str]]] = {}
        for fn in files:
            m = frame_re.match(fn)
            if m:
                prefix = m.group("prefix")
                sep = m.group("sep")
                f_str = m.group("frame")
                ext = m.group("ext")
                key = (prefix, sep, len(f_str), ext.lower())
                groups.setdefault(key, []).append((int(f_str), fn))

        if not groups:
            first_full = os.path.join(folder, files[0])
            return {
                "pattern_path": first_full,
                "first_file": first_full,
                "start_frame": 1001,
                "end_frame": 1001,
                "frame_count": 1,
                "ext": os.path.splitext(files[0])[1].lower(),
            }

        # Pick group matching target_file or largest group
        chosen_key = max(groups.keys(), key=lambda k: len(groups[k]))
        if target_file:
            target_name = os.path.basename(target_file)
            for k, items in groups.items():
                if any(fn == target_name for _, fn in items):
                    chosen_key = k
                    break

        prefix, sep, pad_len, ext = chosen_key
        frames_list = sorted(groups[chosen_key], key=lambda x: x[0])
        start_f = frames_list[0][0]
        end_f = frames_list[-1][0]
        first_fn = frames_list[0][1]

        pattern_name = f"{prefix}{sep}%0{pad_len}d{ext}"
        return {
            "pattern_path": os.path.join(folder, pattern_name),
            "first_file": os.path.join(folder, first_fn),
            "start_frame": start_f,
            "end_frame": end_f,
            "frame_count": len(frames_list),
            "ext": ext,
        }

    def _probe_file(self, file_path: str, is_sequence: bool = False) -> Dict:
        cmd = [
            self.get_ffprobe(),
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            file_path,
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_subprocess_flags(),
                timeout=15,
            )
            if proc.returncode != 0:
                return {}
            data = json.loads(proc.stdout or "{}")
        except Exception:
            return {}

        streams = data.get("streams", [])
        fmt = data.get("format", {})
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if not video_stream:
            return {}

        width = int(video_stream.get("width", 1920))
        height = int(video_stream.get("height", 1080))

        r_frame_rate = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "24/1"
        try:
            fps_frac = fractions.Fraction(r_frame_rate)
            fps_float = float(fps_frac)
        except Exception:
            fps_float = 24.0
            r_frame_rate = "24/1"

        if abs(fps_float - round(fps_float)) < 0.01:
            fps_display = f"{int(round(fps_float))} fps"
        else:
            fps_display = f"{fps_float:.2f} fps"

        nb_frames = 0
        if not is_sequence:
            if video_stream.get("nb_frames") and str(video_stream.get("nb_frames")).isdigit():
                nb_frames = int(video_stream["nb_frames"])
            else:
                dur_str = video_stream.get("duration") or fmt.get("duration")
                if dur_str:
                    try:
                        nb_frames = max(1, int(round(float(dur_str) * fps_float)))
                    except ValueError:
                        nb_frames = 1
            nb_frames = max(1, nb_frames)

        color_trc = video_stream.get("color_transfer", "")
        color_space = video_stream.get("color_space", "")
        color_label = "Unknown"
        if color_trc in ("bt709", "iec61966-2-4") or color_space == "bt709":
            color_label = "Rec.709 / 2.4"
        elif "smpte2084" in color_trc:
            color_label = "Rec.2020 / PQ"
        elif "iec61966-2-1" in color_trc:
            color_label = "sRGB"
        elif "linear" in color_trc:
            color_label = "Linear"

        return {
            "width": width,
            "height": height,
            "fps_float": fps_float,
            "fps_rational": r_frame_rate,
            "fps_display": fps_display,
            "nb_frames": nb_frames,
            "has_audio": audio_stream is not None,
            "color_space": color_label,
        }

    def _parse_filename_tokens(self, filepath: str) -> Dict[str, str]:
        stem = os.path.splitext(os.path.basename(filepath))[0]
        # Strip trailing .1001 frame number if sequence frame
        stem = re.sub(r"[._]\d{4,6}$", "", stem)

        ver_match = re.search(r"(?:^|[_\-\.])([vV]\d{1,4})(?=$|[_\-\.])", stem)
        version = ver_match.group(1) if ver_match else ""

        shot_match = re.match(r"^([A-Za-z0-9]+[-_]\d{3,5})", stem)
        shot_id = shot_match.group(1) if shot_match else ""

        ident = "comp"
        for cand in ("comp", "plate", "denoise", "roto", "prep", "slapcomp", "beauty", "fx", "lgt"):
            if re.search(rf"(?:^|[_\-\.]){cand}(?=$|[_\-\.])", stem, re.IGNORECASE):
                ident = cand
                break

        try:
            mtime = os.path.getmtime(filepath)
            date_str = datetime.date.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except Exception:
            date_str = datetime.date.today().strftime("%Y-%m-%d")

        return {
            "shot_id": shot_id,
            "version": version,
            "identifier": ident,
            "date": date_str,
        }

    # ------------------------------------------------------------------
    # Frame 1001 Thumbnail & Preview Slate Generation
    # ------------------------------------------------------------------
    def extract_first_frame_png(self, clip_info: Dict, output_png: str) -> str:
        """Extracts Frame 1001 from either an image sequence or movie file as an 8-bit PNG."""
        first_src = clip_info.get("first_frame_file") or clip_info.get("source_path", "")

        cmd = [
            self.get_ffmpeg(),
            "-y",
            "-i", first_src,
            "-frames:v", "1",
            "-q:v", "2",
            output_png,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=_subprocess_flags(),
        )
        if proc.returncode != 0 or not os.path.isfile(output_png):
            raise RuntimeError(f"Failed to extract thumbnail frame:\n{proc.stderr}")
        return output_png

    def generate_slate_preview_png(self, clip_info: Dict, output_slate_png: str) -> str:
        """Generates a preview PNG of Frame 1000 Slate for the given clip_info dict."""
        with tempfile.TemporaryDirectory(prefix="vfx_slate_prev_") as tmpdir:
            frame1_png = os.path.join(tmpdir, "frame_1001.png")
            self.extract_first_frame_png(clip_info, frame1_png)
            slate_meta = {
                "shot_id": clip_info.get("shot_id", "SHOT_0010"),
                "version": clip_info.get("version", "V001"),
                "date": clip_info.get("date", ""),
                "frames": clip_info.get("frames_display", "1001 - 1001 (1)"),
                "resolution": clip_info.get("resolution", "1920x1080"),
                "fps": clip_info.get("fps_display", "24 fps"),
                "color_space": clip_info.get("color_space", "Rec.709 / 2.4"),
            }
            settings = load_user_settings()
            return render_slate_image(
                first_frame_image_path=frame1_png,
                output_slate_png=output_slate_png,
                metadata=slate_meta,
                width=int(clip_info.get("width", 1920)),
                height=int(clip_info.get("height", 1080)),
                logo_path=clip_info.get("logo_path", None),
                studio_name=str(clip_info.get("studio_name", settings.get("studio_name", "")) or ""),
                disclaimer=clip_info.get("disclaimer", settings.get("slate_disclaimer", DEFAULT_SLATE_DISCLAIMER)),
            )

    # ------------------------------------------------------------------
    # Full QuickTime Encoding (QT Review H.264 & QT Delivery ProRes 4444)
    # ------------------------------------------------------------------
    def export_clip_qt(
        self,
        clip_info: Dict,
        output_path: str,
        mode: str = "qt_review",
        include_slate: bool = True,
        crf: int = 16,
    ) -> Tuple[bool, str]:
        r"""
        Encodes a single clip (either Image Sequence or Movie) to QuickTime:
        - `mode="qt_review"`: H.264 (`libx264 -crf 16 -pix_fmt yuv420p`), default `include_slate=True` (Frame 1000 Slate + Frames 1001..N).
        - `mode="qt_delivery"`: Apple ProRes 4444 (`prores_ks -profile:v 4 -vendor apl0 -pix_fmt yuva444p10le`), default `include_slate=False`.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        ffmpeg_exe = self.get_ffmpeg()
        is_seq = bool(clip_info.get("is_sequence", False))
        ext = clip_info.get("ext", "").lower()
        width = int(clip_info.get("width", 1920))
        height = int(clip_info.get("height", 1080))
        fps_float = float(clip_info.get("fps_float", 24.0))
        fps_rational = str(clip_info.get("fps_rational", "24/1"))
        start_frame = int(clip_info.get("start_frame", 1001))
        has_audio = bool(clip_info.get("has_audio", False)) and not is_seq

        # Timecode starts at 1000 if 1-frame slate is prepended, otherwise start_frame (1001)
        tc_start_frame = (start_frame - 1) if include_slate else start_frame
        tc_str = frame_to_smpte_timecode(tc_start_frame, fps_float)

        with tempfile.TemporaryDirectory(prefix="vfx_qt_export_") as tmpdir:
            cmd = [ffmpeg_exe, "-y"]

            # Input 0: Optional 1-frame Slate PNG
            if include_slate:
                slate_png = os.path.join(tmpdir, "slate_1000.png")
                self.generate_slate_preview_png(clip_info, slate_png)
                cmd.extend([
                    "-loop", "1",
                    "-framerate", fps_rational,
                    "-t", f"{1.0 / fps_float:.8f}",
                    "-i", slate_png,
                ])

            # Input 1 (or 0): Main clip source (Image Sequence or Movie)
            if is_seq:
                cmd.extend([
                    "-framerate", fps_rational,
                    "-start_number", str(start_frame),
                    "-i", clip_info["sequence_pattern"],
                ])
            else:
                cmd.extend(["-i", clip_info["source_path"]])

            # Filter graph
            if include_slate:
                if has_audio:
                    delay_ms = int(round(1000.0 / fps_float))
                    filter_complex = (
                        f"[0:v]scale={width}:{height},setsar=1,fps={fps_rational},format=yuv444p10le[slate];"
                        f"[1:v]scale={width}:{height},setsar=1,fps={fps_rational},format=yuv444p10le[main];"
                        f"[slate][main]concat=n=2:v=1:a=0[outv];"
                        f"[1:a]aformat=channel_layouts=stereo,adelay={delay_ms}:all=1[outa]"
                    )
                    cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "[outa]"])
                else:
                    filter_complex = (
                        f"[0:v]scale={width}:{height},setsar=1,fps={fps_rational},format=yuv444p10le[slate];"
                        f"[1:v]scale={width}:{height},setsar=1,fps={fps_rational},format=yuv444p10le[main];"
                        f"[slate][main]concat=n=2:v=1:a=0[outv]"
                    )
                    cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]"])
            else:
                # No slate prepended
                cmd.extend(["-vf", f"scale={width}:{height},setsar=1,fps={fps_rational}"])

            # Codec settings
            if mode == "qt_delivery":
                # Apple ProRes 4444
                cmd.extend([
                    "-c:v", "prores_ks",
                    "-profile:v", "4",
                    "-vendor", "apl0",
                    "-bits_per_mb", "8000",
                    "-pix_fmt", "yuva444p10le",
                ])
                if has_audio:
                    cmd.extend(["-c:a", "pcm_s24le"])
            else:
                # H.264 QT Review (ensure even dimensions for yuv420p)
                cmd.extend([
                    "-c:v", "libx264",
                    "-crf", str(crf),
                    "-preset", "fast",
                    "-pix_fmt", "yuv420p",
                    "-color_primaries", "bt709",
                    "-color_trc", "bt709",
                    "-colorspace", "bt709",
                ])
                if has_audio:
                    cmd.extend(["-c:a", "aac", "-b:a", "192k"])

            cmd.extend([
                "-timecode", tc_str,
                "-movflags", "+faststart",
                output_path,
            ])

            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_subprocess_flags(),
            )
            if proc.returncode != 0:
                return False, f"FFmpeg exited with code {proc.returncode}:\n{proc.stderr[-800:]}"

        return True, output_path

    def get_default_export_dir(
        self,
        project_root: str,
        mode: str = "qt_review",
        shot_id: str = "",
        export_folder: str = DEFAULT_EXPORT_FOLDER,
    ) -> str:
        """Returns `<vfx_root>/<export_folder>` (or `<vfx_root>/<export_folder>/<shot_id>` if shot_id is given)."""
        if not project_root:
            return os.path.expanduser("~")
        folder = (export_folder or FOLDER_EXPORTS or DEFAULT_EXPORT_FOLDER).strip()
        if os.path.isabs(folder):
            out_dir = os.path.normpath(folder)
        else:
            out_dir = os.path.join(project_root, folder)
        if shot_id:
            out_dir = os.path.join(out_dir, shot_id)
        os.makedirs(out_dir, exist_ok=True)
        return out_dir
