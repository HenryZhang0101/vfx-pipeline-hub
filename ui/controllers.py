"""Shared UI controllers & workflow helpers reused across Project Manager, DCC Launcher, and Resolve UI."""

import os
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QMessageBox, QWidget

from config import get_ocio_config, load_user_settings, open_path_in_os, save_user_settings
from core.io.excel import merge_shots_from_excel, save_shots_to_excel
from core.io.google_sheets import (
    GoogleSheetSyncClient,
    compare_timestamps,
    format_iso_timestamp,
    smart_merge,
)
from core.models import Project, Shot
from core.storage import ProjectRepository
from core.timecode import format_shot_id
from dccs import DCCRegistry


def get_recorded_recent_projects() -> List[str]:
    """Return all recorded recent VFX project paths from user_settings.json (including offline/missing paths)."""
    settings = load_user_settings()
    recents: List[str] = []
    seen = set()
    for p in settings.get("recent_projects", []):
        raw = str(p or "").strip()
        if not raw:
            continue
        resolved = ProjectRepository.resolve_vfx_root(raw) if os.path.isdir(raw) else os.path.normpath(raw)
        key = os.path.normcase(resolved)
        if key not in seen:
            seen.add(key)
            recents.append(resolved)
    return recents


def get_saved_recent_projects(initial_root: str = "") -> List[str]:
    """Fast startup helper: returns deduplicated existing recent projects from user_settings.json only."""
    settings = load_user_settings()
    recents: List[str] = []
    seen = set()
    for p in settings.get("recent_projects", []):
        if p and os.path.isdir(p):
            resolved = ProjectRepository.resolve_vfx_root(p)
            key = os.path.normcase(resolved)
            if os.path.isdir(resolved) and key not in seen:
                seen.add(key)
                recents.append(resolved)

    if initial_root and os.path.isdir(initial_root):
        norm_init = ProjectRepository.resolve_vfx_root(initial_root)
        key_init = os.path.normcase(norm_init)
        recents = [r for r in recents if os.path.normcase(r) != key_init]
        recents.insert(0, norm_init)

    return recents


def scan_drive_for_projects(existing: Optional[List[str]] = None) -> List[str]:
    """Scans the configured default_projects_root for VFX projects (safe to call from a background thread)."""
    known = set(existing or [])
    found: List[str] = []
    scan_root = (load_user_settings().get("default_projects_root") or "").strip()
    if not scan_root or not os.path.isdir(scan_root):
        return found
    try:
        top_entries = sorted(os.scandir(scan_root), key=lambda e: e.name, reverse=True)
    except OSError:
        return found
    for entry in top_entries:
        name = entry.name
        if name.startswith("_") or name.startswith("."):
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        full_p = entry.path
        if os.path.isfile(os.path.join(full_p, "project.json")):
            norm_p = os.path.normpath(full_p)
            if norm_p not in known:
                known.add(norm_p)
                found.append(norm_p)
            continue
        try:
            sub_entries = sorted(os.scandir(full_p), key=lambda e: e.name)
        except OSError:
            continue
        for sub_entry in sub_entries:
            try:
                if not sub_entry.is_dir():
                    continue
            except OSError:
                continue
            sub_p = sub_entry.path
            if os.path.isfile(os.path.join(sub_p, "project.json")) or sub_entry.name.lower() in ("04_vfx", "05_vfx"):
                norm_sub = os.path.normpath(sub_p)
                if norm_sub not in known:
                    known.add(norm_sub)
                    found.append(norm_sub)
    return found


def discover_recent_projects(initial_root: str = "") -> List[str]:
    """Return deduplicated recent VFX project root directories, scanning default_projects_root if configured."""
    recents = get_saved_recent_projects(initial_root)
    recents.extend(scan_drive_for_projects(recents))
    return recents


def register_recent_project(root_dir: str) -> None:
    """Persist a VFX project root directory at the top of user_settings.json recent_projects."""
    if not root_dir:
        return
    norm_root = ProjectRepository.resolve_vfx_root(root_dir)
    key_root = os.path.normcase(norm_root)
    recents = [r for r in get_recorded_recent_projects() if os.path.normcase(r) != key_root]
    recents.insert(0, norm_root)
    save_user_settings({"recent_projects": recents[:50], "last_project_root": norm_root})


def set_recent_projects(paths: List[str]) -> List[str]:
    """Replace the persisted recent_projects list in user_settings.json and sync last_project_root."""
    cleaned: List[str] = []
    seen = set()
    for p in paths or []:
        raw = str(p or "").strip()
        if not raw:
            continue
        resolved = ProjectRepository.resolve_vfx_root(raw) if os.path.isdir(raw) else os.path.normpath(raw)
        key = os.path.normcase(resolved)
        if key not in seen:
            seen.add(key)
            cleaned.append(resolved)

    settings = load_user_settings()
    last_root = str(settings.get("last_project_root") or "").strip()
    if last_root:
        last_key = os.path.normcase(os.path.normpath(last_root))
        if last_key not in seen:
            last_root = cleaned[0] if cleaned else ""
    elif cleaned:
        last_root = cleaned[0]

    save_user_settings({"recent_projects": cleaned, "last_project_root": last_root})
    return cleaned


def remove_recent_project(root_dir: str) -> List[str]:
    """Remove a VFX project root directory from user_settings.json recent_projects."""
    if not root_dir:
        return get_recorded_recent_projects()
    norm_target = os.path.normcase(ProjectRepository.resolve_vfx_root(root_dir) if os.path.isdir(root_dir) else os.path.normpath(root_dir))
    remaining = [r for r in get_recorded_recent_projects() if os.path.normcase(r) != norm_target]
    return set_recent_projects(remaining)


def load_excel_into_project(
    project: Project,
    repo: ProjectRepository,
    file_path: str,
) -> Tuple[bool, str, int, int]:
    """
    Merge shots from an Excel (.xlsx) file into `project` and persist `project.json`.
    Returns (ok, message, updated_count, added_count).
    """
    if not project:
        return False, "No active project.", 0, 0
    if not file_path or not os.path.isfile(file_path):
        return False, f"Excel file not found:\n{file_path}", 0, 0
    total, updated, added = merge_shots_from_excel(project, file_path)
    if total == 0:
        return False, f"No shots found in Excel file:\n{os.path.basename(file_path)}", 0, 0
    now_ts = format_iso_timestamp()
    project.last_modified = now_ts
    repo.save(project)
    return (
        True,
        f"Loaded {os.path.basename(file_path)}: {updated} updated, {added} added ({len(project.all_shots())} total)",
        updated,
        added,
    )


def toggle_always_on_top(window: QWidget, checked: bool) -> None:
    """Toggle Qt.WindowStaysOnTopHint on any QMainWindow/QDialog."""
    flags = window.windowFlags()
    if checked:
        window.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
    else:
        window.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
    window.show()


def build_shot_dcc_context(
    project: Optional[Project],
    repo: Optional[ProjectRepository],
    shot_obj: Optional[Shot],
    seq: str,
    shot_code: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build normalized shot context dict for DCC scene creation/launch, respecting per-shot overrides."""
    shot_id = shot_obj.shot_id if shot_obj else format_shot_id(seq, shot_code)
    sf = shot_obj.start_frame if shot_obj else 1001
    ef = shot_obj.end_frame if shot_obj else 1065
    fps = (
        shot_obj.fps
        if (shot_obj and shot_obj.fps > 0)
        else (project.fps if project else 24.0)
    )
    w, h = project.resolution if project else (1920.0, 1080.0)
    if shot_obj and shot_obj.resolution:
        w, h = shot_obj.resolution
    w_int, h_int = int(round(float(w))), int(round(float(h)))

    all_media = repo.scan_all_shot_media(project, shot_id) if (project and repo) else []
    plates = [
        m.to_dict() if hasattr(m, "to_dict") else dict(m)
        for m in all_media
        if m.get("category") == "media" and m.get("identifier", "").lower() != "comp"
    ]
    ctx: Dict[str, Any] = {
        "project_name": project.name if project else "",
        "vfx_root": project.vfx_root if project else "",
        "seq": seq,
        "shot": shot_code,
        "start_frame": sf,
        "end_frame": ef,
        "fps": fps,
        "width": w_int,
        "height": h_int,
        "plates": plates,
        "ocio_config": project.effective_ocio_config if project else get_ocio_config(),
    }
    if extra:
        ctx.update(extra)
    return ctx


def launch_dcc_scene(
    dcc_registry: DCCRegistry,
    project: Optional[Project],
    repo: Optional[ProjectRepository],
    shot_obj: Optional[Shot],
    scene_dict: Dict[str, Any],
) -> Tuple[bool, str]:
    """Launch a scanned scene record in its registered DCC plugin (or OS default)."""
    plugin = dcc_registry.get_plugin(scene_dict["dcc_id"]) or dcc_registry.get_plugin_for_extension(scene_dict["ext"])
    if not plugin:
        open_path_in_os(scene_dict["filepath"])
        return True, f"Opened {scene_dict['filename']}"
    ctx = build_shot_dcc_context(
        project,
        repo,
        shot_obj,
        scene_dict["seq"],
        scene_dict["shot"],
        extra={"version": scene_dict["version"], "department": scene_dict["department"]},
    )
    return plugin.launch_scene(scene_dict["filepath"], shot_context=ctx)


def version_up_dcc_scene(
    dcc_registry: DCCRegistry,
    project: Project,
    repo: ProjectRepository,
    shot_obj: Optional[Shot],
    scene_dict: Dict[str, Any],
) -> Tuple[bool, str, str]:
    """Increment scene version (+v####) and write metadata sidecar. Returns (ok, msg, new_path)."""
    dept = scene_dict["department"]
    next_ver = repo.get_next_scene_version(project, scene_dict["shot_id"], dept)
    new_path = repo.build_scene_filepath(project, scene_dict["shot_id"], dept, next_ver, scene_dict["ext"])
    plugin = dcc_registry.get_plugin(scene_dict["dcc_id"]) or dcc_registry.get_plugin_for_extension(scene_dict["ext"])
    ctx = build_shot_dcc_context(
        project,
        repo,
        shot_obj,
        scene_dict["seq"],
        scene_dict["shot"],
        extra={"version": next_ver, "department": dept},
    )

    if plugin:
        ok, msg = plugin.version_up_scene(scene_dict["filepath"], new_path, shot_context=ctx)
    else:
        shutil.copy2(scene_dict["filepath"], new_path)
        ok, msg = True, f"Copied to {os.path.basename(new_path)}"

    if ok:
        settings = load_user_settings()
        repo.write_scene_metadata(
            new_path,
            comment=f"Versioned up from {scene_dict['version']}",
            artist=settings.get("artist_name", ""),
        )
    return ok, msg, new_path


def build_qt_clip_info(
    project: Project,
    slate_tool,
    shot_id: str,
    media_record: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Convert a scanned shot media dict into a BatchQTExportDialog clip_info dict."""
    if not slate_tool or not project:
        return None
    sh_obj = project.get_shot(shot_id)
    sf = sh_obj.start_frame if sh_obj else 1001
    ef = sh_obj.end_frame if sh_obj else 1065
    eff_fps = sh_obj.fps if (sh_obj and sh_obj.fps > 0) else project.fps
    shot_info = {"startFrame": sf, "endFrame": ef, "fps": eff_fps}

    src_path = media_record.get("version_dir") if media_record.get("is_sequence") else media_record.get("first_file_path", "")
    if not src_path or not os.path.exists(src_path):
        src_path = media_record.get("first_file_path", "")
    if not src_path or not os.path.exists(src_path):
        return None

    return slate_tool.inspect_clip_source(
        source_path=src_path,
        shot_id=shot_id,
        identifier=media_record.get("identifier", "comp"),
        version=media_record.get("version", "v0001"),
        shot_info=shot_info,
    )


def collect_latest_shot_media(
    project: Project,
    repo: ProjectRepository,
    shots: List[Shot],
) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    """For each shot, pick the latest comp (or latest playable media). Returns (found_pairs, missing_shot_ids)."""
    found: List[Tuple[str, Dict[str, Any]]] = []
    missing: List[str] = []
    for s_rec in shots:
        all_media = repo.scan_all_shot_media(project, s_rec.shot_id)
        playable = [m for m in all_media if m.get("is_playable")]
        if not playable:
            missing.append(s_rec.shot_id)
            continue
        comp_versions = [m for m in playable if m.get("identifier", "").lower() == "comp"]
        chosen = comp_versions[0] if comp_versions else playable[0]
        found.append((s_rec.shot_id, chosen))
    return found, missing


def open_send_to_client_dialog(
    parent: QWidget,
    project: Optional[Project],
    slate_tool,
    clips: List[Dict[str, Any]],
    initial_mode: str = "qt_review",
    repo: Optional[ProjectRepository] = None,
) -> Any:
    """Open the unified Send to Client (BatchQTExportDialog) window non-modally."""
    from config import DEFAULT_EXPORT_FOLDER
    from ui.dialogs.batch_qt_export_dialog import BatchQTExportDialog

    proj_root = project.vfx_root if project else ""
    exp_folder = project.export_folder if project else DEFAULT_EXPORT_FOLDER
    dlg = BatchQTExportDialog(
        parent,
        slate_tool,
        clips,
        project_root=proj_root,
        initial_mode=initial_mode,
        export_folder=exp_folder,
        project=project,
        repo=repo,
    )
    dlg.setWindowModality(Qt.NonModal)
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    if parent is not None:
        open_dialogs = getattr(parent, "_open_tool_dialogs", None)
        if open_dialogs is None:
            open_dialogs = []
            setattr(parent, "_open_tool_dialogs", open_dialogs)
        open_dialogs.append(dlg)
        dlg.destroyed.connect(lambda _=None, d=dlg: open_dialogs.remove(d) if d in open_dialogs else None)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def save_project_session(project: Project, repo: ProjectRepository, write_excel: bool = True) -> str:
    """Persist project.json and optionally <Project>_ShotList.xlsx. Returns ISO timestamp."""
    now_ts = format_iso_timestamp()
    project.last_modified = now_ts
    repo.save(project)
    if write_excel:
        save_shots_to_excel(
            project.default_excel_path,
            project.all_shots(),
            thumb_dir=project.thumbnails_dir,
            project_name=project.name,
            last_modified=now_ts,
            custom_field_names=project.shot_custom_fields,
            status_colors=project.get_status_property().color_map(),
        )
    return now_ts


def sync_google_sheet_interactive(
    parent: QWidget,
    project: Project,
    repo: ProjectRepository,
    on_pulled: Optional[Callable[[], None]] = None,
) -> Tuple[bool, str]:
    """Run two-way timestamp-aware Google Sheet sync with user prompts."""
    url = project.google_sheet_url
    if not url:
        return False, "NO_URL"

    client = GoogleSheetSyncClient(url)
    QApplication.processEvents()
    ok, msg, remote_meta = client.get_remote_meta()
    if not ok:
        QMessageBox.warning(parent, "Cloud Connection Error", msg)
        return False, msg

    remote_ts = remote_meta.get("last_modified", "")
    local_ts = project.last_modified or format_iso_timestamp()
    comp = compare_timestamps(local_ts, remote_ts)

    if comp == "IN_SYNC" and remote_ts:
        reply = QMessageBox.question(
            parent,
            "Already In Sync",
            f"Local and Google Sheet are in sync ({local_ts}).\n\nForce Push local shots and thumbnails anyway?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return True, "Cancelled"
        comp = "LOCAL_NEWER"

    if comp == "LOCAL_NEWER" or not remote_ts:
        now_ts = format_iso_timestamp()
        push_ok, push_msg, payload = client.push_shots(
            project_name=project.name,
            shots=project.all_shots(),
            last_modified=now_ts,
            thumb_dir=project.thumbnails_dir,
            custom_field_names=project.shot_custom_fields,
        )
        if push_ok:
            project.last_modified = now_ts
            project.last_sync_time = now_ts
            save_project_session(project, repo, write_excel=True)
            drive_err = payload.get("drive_error") if isinstance(payload, dict) else None
            if drive_err:
                QMessageBox.warning(parent, "Drive Permission Needed", f"Shots synced, but Drive reported:\n{drive_err}")
            else:
                QMessageBox.information(parent, "Cloud Push Complete", push_msg)
            return True, push_msg
        QMessageBox.warning(parent, "Cloud Push Failed", push_msg)
        return False, push_msg

    if comp == "REMOTE_NEWER":
        pull_ok, pull_msg, r_shots, r_meta = client.pull_all_shots()
        if pull_ok:
            merged = smart_merge(project.all_shots(), r_shots)
            project.replace_shots(merged)
            r_ts = r_meta.get("last_modified") or format_iso_timestamp()
            project.last_modified = r_ts
            project.last_sync_time = r_ts
            repo.save(project)
            save_shots_to_excel(
                project.default_excel_path,
                merged,
                thumb_dir=project.thumbnails_dir,
                project_name=project.name,
                last_modified=r_ts,
                custom_field_names=project.shot_custom_fields,
                status_colors=project.get_status_property().color_map(),
            )
            if on_pulled:
                on_pulled()
            QMessageBox.information(parent, "Cloud Pull Complete", f"Merged updates from Google Sheet ({r_ts}).")
            return True, pull_msg
        QMessageBox.warning(parent, "Cloud Pull Failed", pull_msg)
        return False, pull_msg

    return True, "OK"
