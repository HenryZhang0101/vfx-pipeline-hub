"""UI-free DaVinci Resolve Scripting API engine for the VFX Pipeline Hub."""

import base64
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import (
    DEFAULT_RESOLVE_MARKER_FIELDS,
    DEFAULT_START_FRAME,
    FUSION_PREF_KEY,
    PIPELINE_ROOT,
    RESOLVE_UTILITY_DIR,
    SEQUENCE_EXTENSIONS,
)
from core.timecode import format_shot_id, frame_to_timecode, timecode_to_frame

_extra_lib_paths = [
    os.path.join(PIPELINE_ROOT, "lib") if sys.platform == "win32" else os.path.join(PIPELINE_ROOT, "lib_mac"),
    os.path.join(RESOLVE_UTILITY_DIR, "lib"),
    os.path.join(RESOLVE_UTILITY_DIR, ".venv", "Lib", "site-packages"),
]
for _p in _extra_lib_paths:
    if _p and os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from PIL import Image as PILImage

    HAS_PILLOW = True
except ImportError:
    PILImage = None
    HAS_PILLOW = False

try:
    import cv2
    import numpy as np

    HAS_OPENCV = True
except ImportError:
    cv2 = None
    np = None
    HAS_OPENCV = False

_qapp_instance = None


def _default_username() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "Artist"


def ensure_qapp():
    """Ensures a QApplication instance exists for Qt widgets."""
    global _qapp_instance
    try:
        from qtpy.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _qapp_instance = QApplication(sys.argv or ["vfx_pipeline"])
            return _qapp_instance
        return app
    except Exception as e:
        print(f"ensure_qapp warning: {e}")
        return None


# =============================================================================
# RESOLVE / FUSION CONNECTION & PROJECT HELPERS
# =============================================================================


def get_resolve_fusion():
    """Returns (resolve, fu, ui, disp, bmd) connected to the active DaVinci Resolve session."""
    import builtins

    bmd_mod = None
    resolve_obj = None
    fu_obj = None
    main_mod = sys.modules.get("__main__")
    for scope in (main_mod.__dict__ if main_mod else {}, builtins.__dict__, globals()):
        if not resolve_obj:
            cand = scope.get("resolve")
            if not cand and "app" in scope:
                cand_app = scope.get("app")
                if cand_app and hasattr(cand_app, "GetProjectManager"):
                    cand = cand_app
            if cand and hasattr(cand, "GetProjectManager"):
                resolve_obj = cand
        if not fu_obj:
            cand_fu = scope.get("fu") or scope.get("fusion")
            if cand_fu and hasattr(cand_fu, "UIManager"):
                fu_obj = cand_fu
        if not bmd_mod:
            cand_bmd = scope.get("bmd")
            if cand_bmd and hasattr(cand_bmd, "scriptapp"):
                bmd_mod = cand_bmd

    scripting_paths = [
        os.path.join(os.environ.get("RESOLVE_SCRIPT_API", ""), "Modules"),
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        "/opt/resolve/Developer/Scripting/Modules",
    ]
    for sp in scripting_paths:
        if sp and os.path.exists(sp) and sp not in sys.path:
            sys.path.insert(0, sp)

    try:
        import DaVinciResolveScript as dvr_bmd

        if not bmd_mod:
            bmd_mod = dvr_bmd
        if not resolve_obj:
            resolve_obj = dvr_bmd.scriptapp("Resolve")
        if not fu_obj:
            fu_obj = dvr_bmd.scriptapp("Fusion")
    except ImportError:
        pass

    if not fu_obj and resolve_obj:
        try:
            fu_obj = resolve_obj.Fusion()
        except Exception:
            pass
    if not resolve_obj and bmd_mod and hasattr(bmd_mod, "scriptapp"):
        try:
            resolve_obj = bmd_mod.scriptapp("Resolve")
        except Exception:
            pass
    if not fu_obj and bmd_mod and hasattr(bmd_mod, "scriptapp"):
        try:
            fu_obj = bmd_mod.scriptapp("Fusion")
        except Exception:
            pass

    ui_obj = fu_obj.UIManager if fu_obj else None
    disp_obj = bmd_mod.UIDispatcher(ui_obj) if (bmd_mod and ui_obj) else None
    if disp_obj is not None and hasattr(disp_obj, "Dispatch"):
        _orig_dispatch = disp_obj.Dispatch

        def _safe_dispatch(ev):
            if not ev or not isinstance(ev, dict):
                return None
            if "window" in ev and "who" not in ev:
                return None
            try:
                return _orig_dispatch(ev)
            except KeyError:
                return None

        object.__setattr__(disp_obj, "Dispatch", _safe_dispatch)
    return resolve_obj, fu_obj, ui_obj, disp_obj, bmd_mod


def get_current_project(resolve):
    if not resolve:
        return None
    pm = resolve.GetProjectManager()
    return pm.GetCurrentProject() if pm else None


def get_project_name(resolve) -> Optional[str]:
    project = get_current_project(resolve)
    return project.GetName() if project else None


def get_timeline_by_name(project, name: str):
    if not project or not name:
        return None
    count = int(project.GetTimelineCount() or 0)
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    return None


def list_timelines(project) -> List[Any]:
    """Return all Timeline objects in the active Resolve project."""
    if not project:
        return []
    result: List[Any] = []
    count = int(project.GetTimelineCount() or 0)
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            result.append(tl)
    return result


def get_timeline_fps(timeline) -> float:
    if not timeline:
        return 24.0
    try:
        val = timeline.GetSetting("timelineFrameRate")
        return float(val) if val else 24.0
    except Exception:
        return 24.0


def get_resolve_specs(resolve) -> Tuple[float, Tuple[int, int]]:
    """Return (fps, (width, height)) from the active Resolve project/timeline."""
    project = get_current_project(resolve)
    if not project:
        return 24.0, (1920, 1080)
    tl = project.GetCurrentTimeline()
    fps = get_timeline_fps(tl) if tl else float(project.GetSetting("timelineFrameRate") or 24.0)
    res_w = int(float(project.GetSetting("timelineResolutionWidth") or 1920))
    res_h = int(float(project.GetSetting("timelineResolutionHeight") or 1080))
    return fps, (res_w, res_h)


def get_or_create_media_pool_folder(media_pool, parent_folder, folder_name: str):
    if not media_pool or not parent_folder:
        return parent_folder
    for sub in parent_folder.GetSubFolderList() or []:
        if sub.GetName() == folder_name:
            return sub
    return media_pool.AddSubFolder(parent_folder, folder_name)


def load_resolve_project_binding(resolve, fu) -> Dict[str, Any]:
    """Read saved VFX project binding for the current Resolve project from Fusion prefs."""
    if not fu:
        return {}
    try:
        proj_key = get_project_name(resolve) or "_default_project"
        sessions = fu.GetData(FUSION_PREF_KEY) or {}
        if not isinstance(sessions, dict):
            return {}
        saved = sessions.get(proj_key) or sessions.get("_default_project")
        return dict(saved) if isinstance(saved, dict) else {}
    except Exception:
        return {}


def save_resolve_project_binding(resolve, fu, data: Dict[str, Any]) -> bool:
    """Persist VFX project binding for the current Resolve project into Fusion prefs."""
    if not fu or not isinstance(data, dict):
        return False
    try:
        proj_key = get_project_name(resolve) or "_default_project"
        sessions = fu.GetData(FUSION_PREF_KEY) or {}
        if not isinstance(sessions, dict):
            sessions = {}
        sessions[proj_key] = dict(data)
        fu.SetData(FUSION_PREF_KEY, sessions)
        return True
    except Exception as e:
        print(f"Warning saving Resolve session binding: {e}")
        return False


# =============================================================================
# THUMBNAIL CAPTURE (PILLOW PRIMARY + OPENCV FALLBACK)
# =============================================================================


def resize_image_file(image_path: str, target_width: int = 720) -> bool:
    """Resize an image file proportionally to target_width using Pillow or OpenCV."""
    if not os.path.exists(image_path):
        return False
    if HAS_PILLOW:
        try:
            with PILImage.open(image_path) as img:
                w, h = img.size
                if w <= target_width:
                    return True
                scale = target_width / float(w)
                new_h = max(1, int(round(h * scale)))
                resample = getattr(getattr(PILImage, "Resampling", PILImage), "LANCZOS", PILImage.BICUBIC)
                resized = img.resize((target_width, new_h), resample)
                resized.save(image_path)
                return True
        except Exception as e:
            print(f"Error resizing {image_path} with Pillow: {e}")
    if HAS_OPENCV:
        try:
            img = cv2.imread(image_path)
            if img is None:
                return False
            h, w = img.shape[:2]
            if w <= target_width:
                return True
            scale = target_width / float(w)
            resized = cv2.resize(img, (target_width, int(h * scale)), interpolation=cv2.INTER_AREA)
            cv2.imwrite(image_path, resized)
            return True
        except Exception as e:
            print(f"Error resizing {image_path} with OpenCV: {e}")
    return False


def resize_image_opencv(image_path: str, target_width: int = 720) -> bool:
    """Backwards-compatible alias for resize_image_file()."""
    return resize_image_file(image_path, target_width=target_width)


def save_clip_thumbnail_to_file(
    resolve,
    timeline,
    start_tc: str,
    target_path: str,
    restore_edit_page: bool = False,
) -> bool:
    """Capture a Resolve Color-page clip thumbnail as PNG using Pillow (or OpenCV fallback)."""
    if not resolve or not timeline or not start_tc:
        return False
    if not HAS_PILLOW and not HAS_OPENCV:
        return False
    try:
        resolve.OpenPage("color")
        timeline.SetCurrentTimecode(start_tc)
        time.sleep(0.5)
        thumb_data = timeline.GetCurrentClipThumbnailImage()
        if not thumb_data or not thumb_data.get("data"):
            if restore_edit_page:
                resolve.OpenPage("edit")
            return False

        width = int(thumb_data["width"])
        height = int(thumb_data["height"])
        raw_bytes = base64.b64decode(thumb_data["data"])
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        if HAS_PILLOW:
            img = PILImage.frombytes("RGB", (width, height), raw_bytes)
            img.save(target_path, format="PNG")
        else:
            nparr = np.frombuffer(raw_bytes, np.uint8)
            cvimg = cv2.cvtColor(nparr.reshape((height, width, 3)), cv2.COLOR_RGB2BGR)
            cv2.imwrite(target_path, cvimg)

        if restore_edit_page:
            resolve.OpenPage("edit")
        return True
    except Exception as e:
        print(f"Error capturing thumbnail at {start_tc}: {e}")
        if restore_edit_page:
            resolve.OpenPage("edit")
        return False


def capture_shot_thumbnail_to_vfx(
    resolve,
    vfx_project,
    shot,
    timeline_name: str = "",
    resolve_project=None,
    restore_edit_page: bool = False,
) -> Optional[str]:
    """Capture a shot's thumbnail from Resolve Color page into <vfx_root>/_thumbnails/<shot_id>.png."""
    if not vfx_project or not shot or not shot.shot_id or not shot.start_tc:
        return None
    resolve_project = resolve_project or get_current_project(resolve)
    if not resolve_project:
        return None
    tl = get_timeline_by_name(resolve_project, timeline_name) or resolve_project.GetCurrentTimeline()
    if not tl:
        return None
    curr_tl = resolve_project.GetCurrentTimeline()
    if not curr_tl or curr_tl.GetName() != tl.GetName():
        resolve_project.SetCurrentTimeline(tl)

    os.makedirs(vfx_project.thumbnails_dir, exist_ok=True)
    target_thumb = os.path.join(vfx_project.thumbnails_dir, f"{shot.shot_id}.png")
    if save_clip_thumbnail_to_file(resolve, tl, shot.start_tc, target_thumb, restore_edit_page=restore_edit_page):
        resize_image_file(target_thumb, 720)
        shot.thumb = target_thumb
        return target_thumb
    return None


# =============================================================================
# TIMELINE INSPECTION, CLIP DISCOVERY & PLAYHEAD NAVIGATION
# =============================================================================


def get_playhead_clip_info(resolve) -> Tuple[Optional[Dict[str, Any]], str]:
    """Inspect the video clip under the active Resolve playhead. Returns (clip_info, error_msg)."""
    project = get_current_project(resolve)
    if not project:
        return None, "No active DaVinci Resolve project found."
    tl = project.GetCurrentTimeline()
    if not tl:
        return None, "No active timeline found in DaVinci Resolve."

    fps = get_timeline_fps(tl)
    playhead_tc = tl.GetCurrentTimecode()
    playhead_f = timecode_to_frame(playhead_tc, fps)
    track_count = int(tl.GetTrackCount("video") or 0)

    found_item = None
    found_track = 1
    for trk_idx in range(1, track_count + 1):
        for it in (tl.GetItemListInTrack("video", trk_idx) or []):
            if it.GetStart() <= playhead_f < it.GetEnd():
                found_item = it
                found_track = trk_idx
                break
        if found_item:
            break

    if not found_item:
        return (
            None,
            f"No video clip found under playhead at {playhead_tc} on timeline '{tl.GetName()}'.\n\n"
            "Please position the playhead over a video clip and try again.",
        )

    start_f = int(found_item.GetStart())
    dur_f = int(found_item.GetDuration())
    end_f = start_f + dur_f
    return (
        {
            "clip_name": found_item.GetName() or "Clip",
            "timeline": tl.GetName(),
            "track": found_track,
            "start_frame": start_f,
            "end_frame": end_f,
            "start_tc": frame_to_timecode(start_f, fps),
            "end_tc": frame_to_timecode(end_f, fps),
            "dur_f": dur_f,
            "fps": fps,
        },
        "",
    )


def collect_timeline_clips(timeline, track_index: int) -> List[Dict[str, Any]]:
    """Collect all non-comp video clips on `track_index` in timeline order (for Batch Assign)."""
    if not timeline:
        return []
    fps = get_timeline_fps(timeline)
    tl_name = timeline.GetName() or ""
    items = timeline.GetItemListInTrack("video", int(track_index)) or []
    results: List[Dict[str, Any]] = []
    for it in sorted(items, key=lambda x: int(x.GetStart())):
        cname = it.GetName() or "Clip"
        if re.search(r"_comp_v\d+", cname, re.I):
            continue
        start_f = int(it.GetStart())
        dur_f = int(it.GetDuration())
        end_f = start_f + dur_f
        results.append(
            {
                "clip_name": cname,
                "timeline": tl_name,
                "track": int(track_index),
                "start_frame": start_f,
                "end_frame": end_f,
                "start_tc": frame_to_timecode(start_f, fps),
                "end_tc": frame_to_timecode(end_f, fps),
                "dur_f": dur_f,
                "fps": fps,
                "marker_name": "",
                "marker_note": "",
                "marker_color": "",
            }
        )
    return results


def collect_timeline_markers(timeline, color_filter: str = "All") -> List[Dict[str, Any]]:
    """Collect timeline markers (optionally filtered by color) matched with underlying video clips."""
    if not timeline:
        return []
    fps = get_timeline_fps(timeline)
    tl_name = timeline.GetName() or ""
    tl_start = int(timeline.GetStartFrame() or 0)
    track_count = int(timeline.GetTrackCount("video") or 0)
    markers = timeline.GetMarkers() or {}

    track_items: List[Tuple[int, List[Any]]] = [
        (trk_idx, timeline.GetItemListInTrack("video", trk_idx) or [])
        for trk_idx in range(1, track_count + 1)
    ]

    results: List[Dict[str, Any]] = []
    for frame_offset in sorted(markers.keys(), key=lambda k: int(k)):
        m_info = markers[frame_offset] or {}
        m_color = str(m_info.get("color") or "")
        if color_filter and color_filter != "All" and m_color.lower() != color_filter.lower():
            continue

        abs_frame = tl_start + int(frame_offset)
        found_item = None
        found_trk = 1
        for trk_idx, items in track_items:
            for it in items:
                cname = it.GetName() or ""
                if re.search(r"_comp_v\d+", cname, re.I):
                    continue
                if int(it.GetStart()) <= abs_frame < int(it.GetEnd()):
                    found_item = it
                    found_trk = trk_idx
                    break
            if found_item:
                break

        if found_item:
            start_f = int(found_item.GetStart())
            dur_f = int(found_item.GetDuration())
            end_f = start_f + dur_f
            clip_name = found_item.GetName() or "Clip"
        else:
            start_f = abs_frame
            dur_f = max(1, int(m_info.get("duration") or 1))
            end_f = start_f + dur_f
            clip_name = str(m_info.get("name") or f"Marker@{frame_to_timecode(start_f, fps)}")

        results.append(
            {
                "clip_name": clip_name,
                "timeline": tl_name,
                "track": found_trk,
                "start_frame": start_f,
                "end_frame": end_f,
                "start_tc": frame_to_timecode(start_f, fps),
                "end_tc": frame_to_timecode(end_f, fps),
                "dur_f": dur_f,
                "fps": fps,
                "marker_name": str(m_info.get("name") or ""),
                "marker_note": str(m_info.get("note") or ""),
                "marker_color": m_color,
            }
        )
    return results


def find_shot_clips_on_timeline(timeline, shot, fps: float) -> List[Tuple[int, Any]]:
    """Find all video track clips matching a Shot (skipping previously loaded _comp_v# clips)."""
    if not timeline or not shot:
        return []
    cut_start_f = timecode_to_frame(shot.start_tc, fps) if shot.start_tc else -1
    shot_clips: List[Tuple[int, Any]] = []
    track_count = int(timeline.GetTrackCount("video") or 0)
    for trk_idx in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", trk_idx) or []
        for it in items:
            name = it.GetName() or ""
            if re.search(r"_comp_v\d+", name, re.I):
                continue
            if cut_start_f >= 0 and abs(int(it.GetStart()) - cut_start_f) <= 2:
                shot_clips.append((trk_idx, it))
                break
            elif shot.name and name == shot.name:
                shot_clips.append((trk_idx, it))
                break
            elif cut_start_f >= 0 and (int(it.GetStart()) <= cut_start_f < int(it.GetEnd())):
                shot_clips.append((trk_idx, it))
                break
    return shot_clips


def find_timeline_comp_version(timeline, start_frame: int, seq: str, shot_code: str) -> Tuple[str, int]:
    """Detect the highest-track comp version (e.g. 'v0003') loaded on `timeline` at `start_frame`."""
    if not timeline:
        return "", -1
    prefix_dash = f"{seq}-{shot_code}".lower()
    prefix_under = f"{seq}_{shot_code}".lower()
    track_count = int(timeline.GetTrackCount("video") or 0)

    for trk_idx in range(track_count, 0, -1):
        for item in (timeline.GetItemListInTrack("video", trk_idx) or []):
            if int(item.GetStart()) <= start_frame < int(item.GetEnd()) or abs(int(item.GetStart()) - start_frame) <= 2:
                c_name = (item.GetName() or "").lower()
                if prefix_dash in c_name or prefix_under in c_name:
                    m = re.search(r"_(v\d{3,4})\b", c_name, re.IGNORECASE)
                    if m:
                        return m.group(1), trk_idx
    return "", -1


def jump_playhead_to_shot(resolve, shot, timeline_name: str = "") -> Tuple[bool, str, bool]:
    """
    Switch to `timeline_name` (if set) and move playhead to `shot.start_tc`.
    If `shot.start_tc` is empty, attempts to discover it from clip name on the timeline.
    Returns (ok, status_message, shot_tc_backfilled).
    """
    project = get_current_project(resolve)
    if not project:
        return False, "No active DaVinci Resolve project.", False
    curr_tl = project.GetCurrentTimeline()
    if timeline_name and (not curr_tl or curr_tl.GetName() != timeline_name):
        target_tl = get_timeline_by_name(project, timeline_name)
        if target_tl:
            project.SetCurrentTimeline(target_tl)
            curr_tl = target_tl

    if not curr_tl:
        return False, "No active timeline in DaVinci Resolve.", False

    backfilled = False
    if not shot.start_tc and shot.name:
        fps = get_timeline_fps(curr_tl)
        for trk_idx in range(1, int(curr_tl.GetTrackCount("video") or 0) + 1):
            for it in (curr_tl.GetItemListInTrack("video", trk_idx) or []):
                if it.GetName() == shot.name:
                    shot.start_tc = frame_to_timecode(int(it.GetStart()), fps)
                    shot.end_tc = frame_to_timecode(int(it.GetEnd()), fps)
                    backfilled = True
                    break
            if backfilled:
                break

    if shot.start_tc:
        curr_tl.SetCurrentTimecode(shot.start_tc)
        return True, f"Jumped playhead to {shot.shot_id} @ {shot.start_tc}", backfilled
    return False, f"No start timecode found for {shot.shot_id}.", False


# =============================================================================
# RENDER & SCENE BACKUP HELPERS
# =============================================================================


def _normalize_sequence_separator(out_dir: str, seq_files: List[str], clean_prefix: str) -> int:
    """
    Ensures image sequence files have a clean '.' between `clean_prefix` and frame digits
    (e.g. 'UHC_0150_plate_v000100086400.exr' -> 'UHC_0150_plate_v0001.00086400.exr')
    without modifying frame numbers.
    """
    renamed = 0
    prefix_low = clean_prefix.lower()
    for old_name in seq_files:
        stem, ext = os.path.splitext(old_name)
        if not stem.lower().startswith(prefix_low):
            continue
        tail = stem[len(clean_prefix):]
        m = re.match(r"^[_.]?(\d+)$", tail)
        if not m:
            continue
        final_name = f"{clean_prefix}.{m.group(1)}{ext.lower()}"
        if final_name == old_name:
            continue
        old_p = os.path.join(out_dir, old_name)
        final_p = os.path.join(out_dir, final_name)
        if os.path.normpath(old_p) != os.path.normpath(final_p):
            try:
                os.replace(old_p, final_p)
                renamed += 1
            except Exception as e:
                print(f"Sequence separator rename warning: {e}")
    return renamed


def normalize_plate_render_files(out_dir: str, prefix: str) -> int:
    """
    Normalizes Resolve Deliver outputs in Plate mode without altering sequence frame numbers:
      - Movie files: strips any stray trailing '_' or '.' before the extension (e.g. `UHC_0150_plate_v0001.mov`).
      - Image sequences: ensures a clean '.' separator before frame digits (e.g. `UHC_0150_plate_v0001.00086400.exr`).
    Returns number of files renamed.
    """
    if not out_dir or not os.path.isdir(out_dir):
        return 0
    clean_prefix = prefix.rstrip("._")
    if not clean_prefix:
        return 0
    files = sorted(
        f for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f))
    )
    if not files:
        return 0

    seq_files: List[str] = []
    single_files: List[str] = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in SEQUENCE_EXTENSIONS:
            seq_files.append(f)
        else:
            single_files.append(f)

    renamed = 0
    if seq_files:
        renamed += _normalize_sequence_separator(out_dir, seq_files, clean_prefix)
    elif len(single_files) == 1:
        old_name = single_files[0]
        ext = os.path.splitext(old_name)[1].lower()
        old_p = os.path.join(out_dir, old_name)
        final_p = os.path.join(out_dir, f"{clean_prefix}{ext}")
        if os.path.normpath(old_p) != os.path.normpath(final_p):
            try:
                os.replace(old_p, final_p)
                renamed += 1
            except Exception as e:
                print(f"Plate movie rename warning: {e}")
    return renamed


def normalize_comp_render_files(
    out_dir: str,
    prefix: str,
    start_frame: int = DEFAULT_START_FRAME,
    renumber_sequence: bool = True,
) -> int:
    """
    In Single Clip mode, Resolve numbers rendered frames using the timeline frame index.
    Renames rendered image sequences in `out_dir` to start at `start_frame` (e.g. 1001)
    using a collision-safe two-pass rename, or normalizes single movie files.
    Returns number of files renamed.
    """
    if not out_dir or not os.path.isdir(out_dir):
        return 0
    clean_prefix = prefix.rstrip("._")
    files = sorted(
        f for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f))
    )
    if not files:
        return 0

    seq_files: List[str] = []
    single_files: List[str] = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in SEQUENCE_EXTENSIONS:
            seq_files.append(f)
        else:
            single_files.append(f)

    renamed = 0
    if seq_files and renumber_sequence:
        temp_paths: List[Tuple[int, str, str]] = []
        for idx, old_name in enumerate(seq_files):
            ext = os.path.splitext(old_name)[1].lower()
            old_p = os.path.join(out_dir, old_name)
            tmp_p = os.path.join(out_dir, f"__tmp_renum_{idx:05d}{ext}")
            try:
                os.replace(old_p, tmp_p)
                temp_paths.append((idx, tmp_p, ext))
            except Exception as e:
                print(f"Temp rename warning: {e}")

        for idx, tmp_p, ext in temp_paths:
            frame_num = int(start_frame) + idx
            final_name = f"{clean_prefix}.{frame_num:04d}{ext}"
            final_p = os.path.join(out_dir, final_name)
            try:
                os.replace(tmp_p, final_p)
                renamed += 1
            except Exception as e:
                print(f"Final rename warning: {e}")
    elif seq_files:
        renamed += _normalize_sequence_separator(out_dir, seq_files, clean_prefix)
    elif single_files:
        for old_name in single_files:
            ext = os.path.splitext(old_name)[1].lower()
            old_p = os.path.join(out_dir, old_name)
            final_p = os.path.join(out_dir, f"{clean_prefix}{ext}")
            if os.path.normpath(old_p) != os.path.normpath(final_p):
                try:
                    os.replace(old_p, final_p)
                    renamed += 1
                except Exception:
                    pass
    return renamed


def save_resolve_comp_scenefiles(
    resolve,
    repo,
    vfx_project,
    shot,
    timeline,
    items_list: List[Any],
    version: str,
    artist: str = "",
) -> str:
    """
    Save the Resolve Timeline (.drt) and any Fusion compositions (.comp) into
    <vfx_root>/<seq>/<shot>/scenes/Compositing/ with a .meta.json sidecar.
    """
    if not vfx_project or not repo or not shot:
        return ""
    task_dir = os.path.join(repo.get_shot_dir(vfx_project, shot.shot_id), "scenes", "Compositing")
    os.makedirs(task_dir, exist_ok=True)

    base_name = f"{format_shot_id(shot.seq, shot.shot)}_comp_{version}"
    saved_scene_path = ""

    if timeline:
        drt_path = os.path.join(task_dir, f"{base_name}.drt")
        try:
            export_type = getattr(resolve, "EXPORT_DRT", 0)
            timeline.Export(drt_path, export_type)
            if os.path.isfile(drt_path):
                saved_scene_path = drt_path
        except Exception as e:
            print(f"Timeline .drt export warning: {e}")

    for it in (items_list or []):
        if not it:
            continue
        try:
            if int(it.GetFusionCompCount() or 0) > 0:
                f_comp = it.GetFusionCompByIndex(1)
                if f_comp:
                    comp_path = os.path.join(task_dir, f"{base_name}.comp")
                    f_comp.Save(comp_path)
                    if os.path.isfile(comp_path) and not saved_scene_path:
                        saved_scene_path = comp_path
                    break
        except Exception as e:
            print(f"Fusion .comp save warning: {e}")

    if saved_scene_path:
        repo.write_scene_metadata(
            saved_scene_path,
            comment="Rendered as Comp from DaVinci Resolve",
            artist=artist or _default_username(),
        )
    return saved_scene_path


def render_shot_job(
    resolve,
    resolve_project,
    timeline,
    job_info: Dict[str, Any],
    comp_mode: bool,
    preset: str,
    track_count: int,
    orig_track_states: Dict[int, bool],
    on_tick: Optional[Callable[[], None]] = None,
) -> Tuple[bool, str]:
    """Execute one Deliver render job for a Plate or Comp entry. Returns (ok, status_msg)."""
    if not resolve or not resolve_project or not timeline:
        return False, "Missing Resolve project or timeline."

    out_dir = job_info["out_dir"]
    os.makedirs(out_dir, exist_ok=True)

    shot = job_info["shot"]
    trk = int(job_info.get("track") or 1)
    cut_dur = int(job_info.get("cut_dur_f") or 0) or (shot.duration_int or 65)
    cut_in = int(job_info.get("cut_start_f", 0))
    cut_out = cut_in + cut_dur - 1
    eff_handles = int(job_info.get("handles", 0))

    resolve.OpenPage("deliver")
    resolve_project.DeleteAllRenderJobs()
    if preset:
        resolve_project.LoadRenderPreset(preset)

    if comp_mode:
        for trk_idx in range(1, track_count + 1):
            has_old_comp = False
            for it_chk in (timeline.GetItemListInTrack("video", trk_idx) or []):
                if int(it_chk.GetStart()) <= cut_in < int(it_chk.GetEnd()):
                    if re.search(r"_comp_v\d+", it_chk.GetName() or "", re.I):
                        has_old_comp = True
                        break
            timeline.SetTrackEnable("video", trk_idx, (not has_old_comp) and orig_track_states.get(trk_idx, True))

        resolve_project.SetCurrentRenderMode(1)  # Single Clip
        resolve_project.SetRenderSettings(
            {
                "SelectAllFrames": False,
                "MarkIn": max(0, cut_in - eff_handles),
                "MarkOut": cut_out + eff_handles,
                "TargetDir": out_dir,
                "CustomName": job_info["custom_name"],
                "UniqueFilenameStyle": 0,
            }
        )
    else:
        for trk_idx in range(1, track_count + 1):
            timeline.SetTrackEnable("video", trk_idx, trk_idx == trk)

        resolve_project.SetCurrentRenderMode(0)  # Individual Clips
        resolve_project.SetRenderSettings(
            {
                "SelectAllFrames": False,
                "MarkIn": cut_in,
                "MarkOut": cut_out,
                "TargetDir": out_dir,
                "CustomName": job_info["custom_name"],
                "UniqueFilenameStyle": 0,
                "AddFrameHandles": eff_handles,
                "UseFullExtents": False,
            }
        )

    job_id = resolve_project.AddRenderJob()
    if not job_id:
        return False, f"Failed to add render job for {shot.shot_id} ({job_info.get('ident', '')})."

    resolve_project.StartRendering(job_id)
    while resolve_project.IsRenderingInProgress():
        if on_tick:
            on_tick()
        time.sleep(0.3)

    job_stat = resolve_project.GetRenderJobStatus(job_id) or {}
    state = job_stat.get("JobStatus", "Complete")
    if state in ("Cancelled", "Failed"):
        return False, f"Render {state.lower()} on {shot.shot_id} ({job_info.get('ident', '')})."
    return True, "Complete"


# =============================================================================
# COMP LOADING & HANDLE TRIMMING
# =============================================================================


def calculate_trimmed_range(
    clip_start_frame: int,
    total_clip_frames: int,
    cut_dur_f: int,
    handles_int: int,
    trim_handles: bool = True,
) -> Tuple[int, int]:
    """Calculate (src_in, src_out) for placing a rendered comp clip onto a timeline cut."""
    src_in = int(clip_start_frame)
    src_out = int(clip_start_frame) + int(total_clip_frames)
    if trim_handles and cut_dur_f > 0 and total_clip_frames > cut_dur_f:
        extra = total_clip_frames - cut_dur_f
        if handles_int > 0 and extra == 2 * handles_int:
            head_handle = handles_int
        elif extra % 2 == 0 and (extra // 2) > 0:
            head_handle = extra // 2
        else:
            head_handle = handles_int if (handles_int > 0 and extra >= handles_int) else (extra // 2)
        src_in = int(clip_start_frame) + head_handle
        src_out = src_in + int(cut_dur_f)
    return src_in, src_out


def load_media_item_to_timeline(
    resolve,
    timeline,
    shot,
    media_item,
    target_trk: int,
    trim_handles: bool = True,
    is_latest: bool = True,
) -> bool:
    """
    Import a `MediaItem` into Media Pool (`VFX_Comps/<SEQ>/<SHOT>`) and place it onto
    `timeline` at `shot.start_tc` on `target_trk` with optional handle trimming.
    """
    resolve_project = get_current_project(resolve)
    if not resolve_project or not timeline or not shot or not media_item:
        return False

    media_pool = resolve_project.GetMediaPool()
    if not media_pool:
        return False
    root_folder = media_pool.GetRootFolder()

    curr_tl = resolve_project.GetCurrentTimeline()
    if not curr_tl or curr_tl.GetName() != timeline.GetName():
        resolve_project.SetCurrentTimeline(timeline)

    fps = get_timeline_fps(timeline)
    cut_start_f = timecode_to_frame(shot.start_tc, fps) if shot.start_tc else 0
    cut_dur_f = shot.duration_int or (
        (timecode_to_frame(shot.end_tc, fps) - cut_start_f) if shot.end_tc else 0
    )

    comps_bin = get_or_create_media_pool_folder(media_pool, root_folder, "VFX_Comps")
    seq_bin = get_or_create_media_pool_folder(media_pool, comps_bin, shot.seq)
    shot_bin = get_or_create_media_pool_folder(media_pool, seq_bin, shot.shot)
    media_pool.SetCurrentFolder(shot_bin)

    is_seq = bool(getattr(media_item, "is_sequence", False))
    if is_seq:
        seq_pattern = getattr(media_item, "nuke_pattern", "") or getattr(media_item, "first_file_path", "")
        seq_pattern = os.path.normpath(seq_pattern) if seq_pattern else ""
        imported = media_pool.ImportMedia(
            [
                {
                    "FilePath": seq_pattern,
                    "StartIndex": int(getattr(media_item, "start_frame", DEFAULT_START_FRAME)),
                    "EndIndex": int(getattr(media_item, "end_frame", DEFAULT_START_FRAME)),
                }
            ]
        )
    else:
        single_path = getattr(media_item, "first_file_path", "")
        imported = media_pool.ImportMedia([single_path]) if single_path else None

    if not imported:
        return False

    mpi = imported[0]
    try:
        clip_start_frame = int(mpi.GetClipProperty("Start"))
    except Exception:
        clip_start_frame = int(getattr(media_item, "start_frame", 0)) if is_seq else 0

    try:
        total_clip_frames = int(mpi.GetClipProperty("Frames") or getattr(media_item, "frame_count", 0) or cut_dur_f)
    except Exception:
        total_clip_frames = int(getattr(media_item, "frame_count", 0) or cut_dur_f)

    src_in, src_out = calculate_trimmed_range(
        clip_start_frame=clip_start_frame,
        total_clip_frames=total_clip_frames,
        cut_dur_f=cut_dur_f,
        handles_int=shot.handles_int,
        trim_handles=trim_handles,
    )

    existing_items_on_trk = [
        item
        for item in (timeline.GetItemListInTrack("video", int(target_trk)) or [])
        if abs(int(item.GetStart()) - cut_start_f) <= 2
    ]
    if existing_items_on_trk and hasattr(timeline, "DeleteClips"):
        try:
            timeline.DeleteClips(existing_items_on_trk)
        except Exception:
            pass

    clip_info = [
        {
            "mediaPoolItem": mpi,
            "startFrame": int(src_in),
            "endFrame": int(src_out),
            "recordFrame": int(cut_start_f),
            "trackIndex": int(target_trk),
        }
    ]
    appended = media_pool.AppendToTimeline(clip_info)
    if not appended:
        return False

    try:
        appended[0].SetClipColor("Green" if is_latest else "Yellow")
    except Exception:
        pass
    return True


# =============================================================================
# VFX TIMELINE MARKER SYNC
# =============================================================================

DEFAULT_MARKER_FIELDS: List[str] = list(DEFAULT_RESOLVE_MARKER_FIELDS)

BUILTIN_MARKER_FIELD_DEFS: List[Tuple[str, str]] = [
    ("status", "Status"),
    ("assign", "Artist (if assigned)"),
    ("notes", "Notes"),
    ("handles", "Handles"),
    ("duration", "Duration"),
    ("clip_name", "Clip Name"),
    ("comp_version", "Comp Version"),
    ("resolution", "Resolution / FPS"),
]

_BUILTIN_MARKER_KEYS = {k for k, _ in BUILTIN_MARKER_FIELD_DEFS}


def format_shot_marker_note(shot, marker_fields: Optional[List[str]] = None) -> str:
    """
    Build the Resolve timeline marker note text for `shot` according to `marker_fields`.
    Defaults to `DEFAULT_MARKER_FIELDS` (`["status", "assign", "notes"]`).
    Any field that is empty on `shot` (e.g. unassigned artist or blank notes) is omitted.
    """
    if not shot:
        return ""

    selected = list(DEFAULT_MARKER_FIELDS) if marker_fields is None else list(marker_fields)
    selected_set = set(selected)
    lines: List[str] = []

    if "status" in selected_set and getattr(shot, "status", ""):
        lines.append(f"Status: {shot.status}")
    if "assign" in selected_set and str(getattr(shot, "assign", "") or "").strip():
        lines.append(f"Assign: {str(shot.assign).strip()}")
    if "handles" in selected_set and str(getattr(shot, "handles", "") or "").strip():
        lines.append(f"Handles: {shot.handles}f")
    if "duration" in selected_set and str(getattr(shot, "duration", "") or "").strip():
        lines.append(f"Duration: {shot.duration}")
    if "clip_name" in selected_set and str(getattr(shot, "name", "") or "").strip():
        lines.append(f"Clip: {shot.name}")
    if "comp_version" in selected_set and str(getattr(shot, "comp_version", "") or "").strip():
        lines.append(f"Comp: {shot.comp_version}")
    if "resolution" in selected_set:
        res_str = getattr(shot, "resolution_str", "") or ""
        fps_val = float(getattr(shot, "fps", 0.0) or 0.0)
        if res_str and fps_val > 0:
            lines.append(f"Specs: {res_str} @ {fps_val:g}fps")
        elif res_str:
            lines.append(f"Specs: {res_str}")
        elif fps_val > 0:
            lines.append(f"Specs: {fps_val:g}fps")

    custom_fields = getattr(shot, "custom_fields", None) or {}
    for field_key in selected:
        if field_key in _BUILTIN_MARKER_KEYS:
            continue
        prop_name = field_key[7:] if field_key.startswith("custom:") else field_key
        val = str(custom_fields.get(prop_name, "") or "").strip()
        if val:
            lines.append(f"{prop_name}: {val}")

    notes_txt = str(getattr(shot, "notes", "") or "").strip()
    if "notes" in selected_set and notes_txt:
        lines.append(notes_txt)

    return "\n".join(lines)


def update_shot_vfx_marker(
    resolve,
    shot,
    timeline_name: str = "",
    marker_fields: Optional[List[str]] = None,
) -> bool:
    """Creates or updates a VFX marker on the Resolve timeline for the given Shot."""
    try:
        project = get_current_project(resolve)
        if not project:
            return False
        tl = get_timeline_by_name(project, timeline_name) or project.GetCurrentTimeline()
        if not tl or not shot or not shot.shot_id or not shot.start_tc:
            return False

        fps = get_timeline_fps(tl)
        frame_id = int(timecode_to_frame(shot.start_tc, fps) - int(tl.GetStartFrame() or 0))
        if frame_id < 0:
            return False

        st_low = (shot.status or "").strip().lower()
        if st_low in ("approved", "final"):
            marker_color = "Green"
        elif st_low in ("review", "client review"):
            marker_color = "Cyan"
        elif st_low in ("wip", "in progress"):
            marker_color = "Yellow"
        elif st_low in ("omit", "hold", "blocked"):
            marker_color = "Red"
        else:
            marker_color = "Lemon"

        marker_note = format_shot_marker_note(shot, marker_fields=marker_fields)

        for f_id, m_info in list((tl.GetMarkers() or {}).items()):
            if m_info.get("name") == shot.shot_id or int(f_id) == frame_id:
                tl.DeleteMarkerAtFrame(int(f_id))

        return bool(tl.AddMarker(frame_id, marker_color, shot.shot_id, marker_note, 1))
    except Exception as ex:
        print(f"Failed to update VFX marker for {getattr(shot, 'shot_id', '?')}: {ex}")
        return False


def sync_shots_vfx_markers(
    resolve,
    vfx_project,
    shots: List[Any],
    marker_fields: Optional[List[str]] = None,
) -> Tuple[int, int]:
    """Batch-sync VFX timeline markers for `shots`. Returns (updated_count, skipped_count)."""
    updated = 0
    skipped = 0
    for s in (shots or []):
        tl_name = ""
        if vfx_project and s.seq and s.seq in vfx_project.sequences:
            tl_name = vfx_project.sequences[s.seq].timeline or ""
        if update_shot_vfx_marker(resolve, s, timeline_name=tl_name, marker_fields=marker_fields):
            updated += 1
        else:
            skipped += 1
    return updated, skipped
