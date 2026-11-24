import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import (
    ASSET_3D_EXTENSIONS,
    DEFAULT_HANDLES,
    DEFAULT_SHOT_DEPARTMENTS,
    DEFAULT_START_FRAME,
    MEDIA_EXTENSIONS,
    SEQUENCE_EXTENSIONS,
    get_default_artist_name,
)
from core.models import MediaItem, Project, Shot, make_media_key
from core.models.media import format_mtime
from core.models.project import infer_project_name_from_path
from core.thumbnails import get_media_thumbnail_path
from core.timecode import format_shot_id, split_shot_id


EXT_TO_DCC = {
    ".nk": "nuke",
    ".hip": "houdini",
    ".hiplc": "houdini",
    ".hipnc": "houdini",
    ".blend": "blender",
    ".ma": "maya",
    ".mb": "maya",
    ".comp": "resolve",
    ".drt": "resolve",
}

_SEQ_FRAME_RE = re.compile(r"^(.*?)([._])(\d{3,6})\.([a-zA-Z0-9]+)$")
_VERSION_DIR_RE = re.compile(r"^v\d+", re.IGNORECASE)
_VERSION_NUM_RE = re.compile(r"^v(\d+)$", re.IGNORECASE)
_SCENE_VER_RE = re.compile(r"_(v\d{3,5})$", re.IGNORECASE)
_SCENE_NEXT_VER_RE = re.compile(r"_v(\d{3,5})\.[a-zA-Z0-9]+$", re.IGNORECASE)
_VALID_MEDIA_ASSET_EXTS = frozenset(MEDIA_EXTENSIONS) | frozenset(ASSET_3D_EXTENSIONS)
_SEQUENCE_EXTS = frozenset(SEQUENCE_EXTENSIONS)
_MEDIA_EXTS = frozenset(MEDIA_EXTENSIONS)
_IS_WIN32 = sys.platform == "win32"


class ProjectRepository:
    """
    Unified filesystem & JSON persistence for a VFX Project.
    Manages <vfx_root>/project.json, _thumbnails, _export (or custom export_folder),
    shot directories, scene files, plates, renders, and 3D caches.
    """

    def __init__(self) -> None:
        self._media_cache: Dict[Tuple[str, str], Tuple[Tuple[Any, ...], List[MediaItem]]] = {}
        self._media_cache_lock = threading.Lock()

    def invalidate_media_cache(self, shot_id: Optional[str] = None, project: Optional[Project] = None) -> None:
        """Clears cached media scans (all entries, or just a specific shot)."""
        with self._media_cache_lock:
            if not shot_id:
                if project and project.vfx_root:
                    norm_root = os.path.normcase(os.path.normpath(project.vfx_root))
                    stale = [k for k in self._media_cache if k[0] == norm_root]
                    for k in stale:
                        self._media_cache.pop(k, None)
                else:
                    self._media_cache.clear()
                return
            seq, shot = split_shot_id(shot_id)
            norm_sid = format_shot_id(seq, shot) if (seq and shot) else shot_id.strip()
            norm_root = os.path.normcase(os.path.normpath(project.vfx_root)) if (project and project.vfx_root) else ""
            stale = [
                k for k in self._media_cache
                if k[1] == norm_sid and (not norm_root or k[0] == norm_root)
            ]
            for k in stale:
                self._media_cache.pop(k, None)

    @staticmethod
    def _shot_media_dir_signature(shot_dir: str) -> Tuple[Any, ...]:
        """
        Cheap directory signature for <shot>/media and <shot>/assets plus their immediate
        identifier subdirectories. On Windows, os.scandir() fetches mtimes in a single
        directory read with zero extra stat syscalls.
        """
        sig: List[Any] = []
        for cat_name in ("media", "assets"):
            cat_root = os.path.join(shot_dir, cat_name)
            try:
                cat_st = os.stat(cat_root)
                sig.append((cat_name, cat_st.st_mtime_ns))
            except OSError:
                sig.append((cat_name, 0))
                continue
            id_sigs: List[Tuple[str, int]] = []
            try:
                with os.scandir(cat_root) as it:
                    for entry in it:
                        try:
                            if entry.is_dir():
                                id_sigs.append((entry.name, entry.stat().st_mtime_ns))
                        except OSError:
                            continue
            except OSError:
                pass
            id_sigs.sort()
            sig.append(tuple(id_sigs))
        return tuple(sig)

    @staticmethod
    def resolve_vfx_root(root_dir: str) -> str:
        """
        Normalizes a user-provided project path to the VFX project root.
        If `root_dir` does not directly contain `project.json` or `_thumbnails`,
        but has an existing `04_VFX/project.json` child (legacy outer show folder),
        automatically resolves to `<root_dir>/04_VFX`.
        """
        if not root_dir:
            return ""
        norm = os.path.normpath(root_dir)
        if os.path.isfile(os.path.join(norm, "project.json")) or os.path.isdir(os.path.join(norm, "_thumbnails")):
            return norm
        legacy_vfx = os.path.join(norm, "04_VFX")
        if os.path.isfile(os.path.join(legacy_vfx, "project.json")) or os.path.isdir(os.path.join(legacy_vfx, "_thumbnails")):
            return legacy_vfx
        return norm

    def initialize_structure(self, project: Project) -> Tuple[bool, str]:
        """Creates the VFX project directory structure (_thumbnails, export_folder, and project.json)."""
        if not project.root_dir:
            return False, "VFX project root directory is not set."
        try:
            os.makedirs(project.vfx_root, exist_ok=True)
            os.makedirs(project.thumbnails_dir, exist_ok=True)
            if project.exports_dir:
                os.makedirs(project.exports_dir, exist_ok=True)
            if not os.path.isfile(project.project_json_path):
                self.save(project)
            return True, f"Initialized VFX project structure at {project.vfx_root}"
        except Exception as e:
            return False, f"Failed to initialize VFX project structure: {e}"

    def load(self, root_dir: str, project_name: str = "") -> Project:
        """Loads Project from <vfx_root>/project.json, or creates a new Project instance if not yet saved."""
        vfx_root = self.resolve_vfx_root(root_dir)
        default_name = project_name or infer_project_name_from_path(vfx_root)
        project = Project(root_dir=vfx_root, name=default_name)
        if os.path.isfile(project.project_json_path):
            try:
                with open(project.project_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    loaded = Project.from_dict(data, root_dir=vfx_root)
                    if not loaded.name:
                        loaded.name = default_name
                    return loaded
            except Exception as e:
                print(f"Warning reading project.json: {e}")
        return project

    def save(self, project: Project) -> bool:
        """Persists Project (specs, artists, sequences, shots, export_folder) to <vfx_root>/project.json."""
        if not project.root_dir:
            return False
        try:
            os.makedirs(project.vfx_root, exist_ok=True)
            if project.exports_dir:
                os.makedirs(project.exports_dir, exist_ok=True)
            with open(project.project_json_path, "w", encoding="utf-8") as f:
                json.dump(project.to_dict(), f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving project.json: {e}")
            return False

    # =========================================================================
    # SHOT DIRECTORY & DISCOVERY
    # =========================================================================

    def get_shot_dir(self, project: Project, shot_id: str) -> str:
        seq, shot = split_shot_id(shot_id)
        return os.path.join(project.vfx_root, seq, shot)

    def ensure_shot(
        self,
        project: Project,
        shot_id: str,
        start_frame: int = DEFAULT_START_FRAME,
        handles: int = DEFAULT_HANDLES,
        departments: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[bool, str]:
        """Creates the shot directory structure and registers the shot in Project if missing."""
        if not project.root_dir:
            return False, "Project root directory is not set."
        seq, shot_code = split_shot_id(shot_id)
        if not seq or not shot_code:
            return False, f"Invalid Shot ID: {shot_id}"

        norm_id = format_shot_id(seq, shot_code)
        shot_dir = self.get_shot_dir(project, norm_id)
        depts = departments or DEFAULT_SHOT_DEPARTMENTS
        try:
            for sub in ("media", "assets", "scenes"):
                os.makedirs(os.path.join(shot_dir, sub), exist_ok=True)
            for dept in depts:
                dept_name = dept.get("name", "") if isinstance(dept, dict) else str(dept)
                if dept_name:
                    os.makedirs(os.path.join(shot_dir, "scenes", dept_name), exist_ok=True)

            existing = project.get_shot(norm_id)
            if existing is None:
                project.add_shot(
                    Shot(
                        shot_id=norm_id,
                        start_frame=start_frame,
                        handles=str(handles),
                    )
                )
            else:
                existing.start_frame = start_frame
                existing.handles = str(handles)
            self.save(project)
            return True, f"Shot '{norm_id}' ready at {shot_dir}"
        except Exception as e:
            return False, f"Failed creating shot {norm_id}: {e}"

    def discover_disk_shots(self, project: Project) -> List[Tuple[str, str]]:
        """Scans <vfx_root>/<SEQ>/<SHOT>/ on disk and returns sorted (seq, shot) pairs."""
        results: List[Tuple[str, str]] = []
        if not project.vfx_root or not os.path.isdir(project.vfx_root):
            return results
        exp_dir_norm = os.path.normcase(os.path.normpath(project.exports_dir)) if project.exports_dir else ""
        exp_base_low = os.path.basename(project.exports_dir).lower() if project.exports_dir else ""
        try:
            seq_entries = sorted(os.scandir(project.vfx_root), key=lambda e: e.name)
        except OSError:
            return results
        for seq_entry in seq_entries:
            seq = seq_entry.name
            if seq.startswith("_") or seq.startswith("."):
                continue
            if exp_base_low and seq.lower() == exp_base_low:
                continue
            try:
                if not seq_entry.is_dir():
                    continue
            except OSError:
                continue
            seq_path = seq_entry.path
            if exp_dir_norm and os.path.normcase(os.path.normpath(seq_path)) == exp_dir_norm:
                continue
            try:
                shot_entries = sorted(os.scandir(seq_path), key=lambda e: e.name)
            except OSError:
                continue
            for shot_entry in shot_entries:
                shot = shot_entry.name
                if shot.startswith("_") or shot.startswith("."):
                    continue
                try:
                    if not shot_entry.is_dir():
                        continue
                except OSError:
                    continue
                if project.is_shot_ignored(format_shot_id(seq, shot)):
                    continue
                results.append((seq, shot))
        return results

    def remove_shot(
        self,
        project: Project,
        shot_id: str,
        delete_from_disk: bool = False,
    ) -> Tuple[bool, str]:
        """
        Removes a shot from project.json.

        - If `delete_from_disk` is False, keeps the shot folder and all media/assets/scenes
          untouched on disk and records the Shot ID in `project.ignored_shots` so disk
          auto-discovery does not immediately re-add it.
        - If `delete_from_disk` is True, permanently deletes `<vfx_root>/<SEQ>/<SHOT>/`
          (and removes the parent `<SEQ>` folder if empty), along with its cached thumbnails
          and media metadata.
        """
        if not project.root_dir or not shot_id:
            return False, "Invalid project or Shot ID."

        seq, shot_code = split_shot_id(shot_id)
        norm_id = format_shot_id(seq, shot_code)
        shot_dir = self.get_shot_dir(project, norm_id)

        if delete_from_disk:
            if os.path.isdir(shot_dir):
                try:
                    shutil.rmtree(shot_dir)
                    seq_dir = os.path.dirname(shot_dir)
                    if os.path.isdir(seq_dir) and not os.listdir(seq_dir):
                        os.rmdir(seq_dir)
                except Exception as e:
                    return False, f"Failed deleting shot folder for '{norm_id}': {e}"

            thumb_path = os.path.join(project.thumbnails_dir, f"{norm_id}.png")
            if os.path.isfile(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass
            media_thumb_dir = os.path.join(project.thumbnails_dir, "media", norm_id)
            if os.path.isdir(media_thumb_dir):
                try:
                    shutil.rmtree(media_thumb_dir)
                except Exception:
                    pass

            meta_prefix = f"{norm_id}|"
            for mk in [k for k in list(project.media_metadata.keys()) if k.startswith(meta_prefix)]:
                project.media_metadata.pop(mk, None)

            project.unignore_shot(norm_id)
            project.remove_shot(norm_id)
            self.save(project)
            return True, f"Removed shot '{norm_id}' and deleted folder from disk."

        if os.path.isdir(shot_dir):
            project.ignore_shot(norm_id)
        project.remove_shot(norm_id)
        self.save(project)
        return True, f"Removed shot '{norm_id}' from project (kept files on disk)."

    def remove_empty_shot(self, project: Project, shot_id: str) -> Tuple[bool, str]:
        """
        Safely removes a shot from project.json and deletes its empty shot directory on disk
        ONLY if no media, asset, or scene files exist inside 04_VFX/<SEQ>/<SHOT>/.
        """
        if not project.root_dir or not shot_id:
            return False, "Invalid project or Shot ID."

        seq, shot_code = split_shot_id(shot_id)
        norm_id = format_shot_id(seq, shot_code)
        shot_dir = self.get_shot_dir(project, norm_id)

        media_items = self.scan_all_shot_media(project, norm_id)
        scene_items = self.scan_shot_scenefiles(project, norm_id, department_filter="all")
        if media_items or scene_items:
            counts = []
            if media_items:
                counts.append(f"{len(media_items)} media/asset version(s)")
            if scene_items:
                counts.append(f"{len(scene_items)} scene file(s)")
            return (
                False,
                f"Cannot remove '{norm_id}' because it contains {' and '.join(counts)} on disk.",
            )

        if os.path.isdir(shot_dir):
            non_empty_files: List[str] = []
            for root, _, files in os.walk(shot_dir):
                for fname in files:
                    if fname.lower() in ("thumbs.db", ".ds_store", "desktop.ini"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fname), shot_dir)
                    non_empty_files.append(rel)
            if non_empty_files:
                sample = ", ".join(non_empty_files[:3])
                return (
                    False,
                    f"Cannot remove '{norm_id}' because files exist in shot folder ({sample}).",
                )
            try:
                shutil.rmtree(shot_dir)
                seq_dir = os.path.dirname(shot_dir)
                if os.path.isdir(seq_dir) and not os.listdir(seq_dir):
                    os.rmdir(seq_dir)
            except Exception as e:
                return False, f"Failed removing empty directory for '{norm_id}': {e}"

        thumb_path = os.path.join(project.thumbnails_dir, f"{norm_id}.png")
        if os.path.isfile(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass
        media_thumb_dir = os.path.join(project.thumbnails_dir, "media", norm_id)
        if os.path.isdir(media_thumb_dir):
            try:
                shutil.rmtree(media_thumb_dir)
            except Exception:
                pass

        project.remove_shot(norm_id)
        self.save(project)
        return True, f"Removed empty shot '{norm_id}'."

    # =========================================================================
    # SCENE FILES
    # =========================================================================

    def scan_shot_scenefiles(
        self,
        project: Project,
        shot_id: str,
        department_filter: str = "",
    ) -> List[Dict[str, Any]]:
        """Scans 04_VFX/<SEQ>/<SHOT>/scenes/<dept>/ for DCC scene files."""
        scenes: List[Dict[str, Any]] = []
        if not project.root_dir or not shot_id:
            return scenes

        seq, shot = split_shot_id(shot_id)
        scenes_root = os.path.join(self.get_shot_dir(project, shot_id), "scenes")
        if not os.path.isdir(scenes_root):
            return scenes

        try:
            dept_entries = sorted(os.scandir(scenes_root), key=lambda e: e.name)
        except OSError:
            return scenes

        for dept_entry in dept_entries:
            dept = dept_entry.name
            if department_filter and department_filter.lower() != "all" and dept.lower() != department_filter.lower():
                continue
            try:
                if not dept_entry.is_dir():
                    continue
            except OSError:
                continue
            dept_path = dept_entry.path

            try:
                file_entries = sorted(os.scandir(dept_path), key=lambda e: e.name)
            except OSError:
                continue
            file_names = {fe.name for fe in file_entries}
            for f_entry in file_entries:
                fname = f_entry.name
                try:
                    if not f_entry.is_file():
                        continue
                except OSError:
                    continue
                if fname.endswith("~") or fname.endswith(".autosave") or fname.endswith(".bak"):
                    continue
                base_no_ext, ext = os.path.splitext(fname)
                ext_low = ext.lower()
                if ext_low not in EXT_TO_DCC:
                    continue

                m_ver = _SCENE_VER_RE.search(base_no_ext)
                version = m_ver.group(1).lower() if m_ver else "v0001"

                meta = {}
                meta_fname = f"{base_no_ext}.meta.json"
                if meta_fname in file_names:
                    meta_path = os.path.join(dept_path, meta_fname)
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            meta = json.load(mf)
                    except Exception:
                        pass

                try:
                    mtime = f_entry.stat().st_mtime
                except OSError:
                    mtime = 0.0
                scenes.append({
                    "seq": seq,
                    "shot": shot,
                    "shot_id": format_shot_id(seq, shot),
                    "department": dept,
                    "version": version,
                    "filename": fname,
                    "filepath": os.path.normpath(f_entry.path),
                    "ext": ext_low,
                    "dcc_id": EXT_TO_DCC.get(ext_low, "unknown"),
                    "user": meta.get("user") or get_default_artist_name(),
                    "comment": meta.get("comment", ""),
                    "mtime": mtime,
                    "mtime_str": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "",
                })

        scenes.sort(key=lambda s: (s["department"], s["version"]), reverse=True)
        return scenes

    def get_next_scene_version(self, project: Project, shot_id: str, department: str) -> str:
        """Returns the next v#### version for a shot's department."""
        dept_dir = os.path.join(self.get_shot_dir(project, shot_id), "scenes", department)
        if not os.path.isdir(dept_dir):
            return "v0001"
        max_v = 0
        for fname in os.listdir(dept_dir):
            if fname.endswith(".json") or fname.endswith("~") or fname.endswith(".bak"):
                continue
            m = re.search(r"_v(\d{3,5})\.[a-zA-Z0-9]+$", fname, re.IGNORECASE)
            if m:
                max_v = max(max_v, int(m.group(1)))
        return f"v{max_v + 1:04d}"

    def build_scene_filepath(
        self,
        project: Project,
        shot_id: str,
        department: str,
        version: str,
        ext: str,
        task_label: str = "",
    ) -> str:
        if not ext.startswith("."):
            ext = f".{ext}"
        if not version.startswith("v"):
            version = f"v{int(version):04d}"
        dept_dir = os.path.join(self.get_shot_dir(project, shot_id), "scenes", department)
        os.makedirs(dept_dir, exist_ok=True)
        seq, shot = split_shot_id(shot_id)
        norm_id = format_shot_id(seq, shot)
        tag = task_label or department.lower()
        fname = f"{norm_id}_{tag}_{version}{ext}"
        return os.path.join(dept_dir, fname)

    def write_scene_metadata(
        self,
        scene_filepath: str,
        comment: str = "",
        artist: str = "",
    ) -> str:
        base_no_ext, _ = os.path.splitext(scene_filepath)
        meta_path = f"{base_no_ext}.meta.json"
        payload = {
            "user": artist or get_default_artist_name(),
            "comment": comment,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"Warning writing scene metadata: {e}")
        return meta_path

    # =========================================================================
    # MEDIA & ASSETS (<identifier>/<version>/<file>)
    # =========================================================================

    def get_plate_export_dir_and_name(
        self,
        project: Project,
        shot_id: str,
        identifier: str = "plate",
        version: str = "v0001",
    ) -> Tuple[str, str]:
        seq, shot = split_shot_id(shot_id)
        norm_id = format_shot_id(seq, shot)
        out_dir = os.path.join(self.get_shot_dir(project, norm_id), "media", identifier, version)
        os.makedirs(out_dir, exist_ok=True)
        base_name = f"{norm_id}_{identifier}_{version}"
        return out_dir, base_name

    def get_next_media_version(
        self,
        project: Project,
        shot_id: str,
        category: str,
        identifier: str,
    ) -> str:
        shot_dir = self.get_shot_dir(project, shot_id)
        cat_folder = "assets" if category == "assets" else "media"
        base_dir = os.path.join(shot_dir, cat_folder, identifier)

        if not os.path.isdir(base_dir):
            return "v0001"
        max_v = 0
        for entry in os.listdir(base_dir):
            if os.path.isdir(os.path.join(base_dir, entry)):
                m = re.match(r"^v(\d+)$", entry, re.IGNORECASE)
                if m:
                    max_v = max(max_v, int(m.group(1)))
        return f"v{max_v + 1:04d}"

    def scan_all_shot_media(self, project: Project, shot_id: str, use_cache: bool = True) -> List[MediaItem]:
        """Scans media/<identifier>/<version>/ and assets/<identifier>/<version>/ for a shot."""
        items: List[MediaItem] = []
        if not project.root_dir or not shot_id:
            return items

        seq, shot = split_shot_id(shot_id)
        norm_sid = format_shot_id(seq, shot)
        shot_dir = self.get_shot_dir(project, shot_id)
        if not os.path.isdir(shot_dir):
            return items

        cache_key = (os.path.normcase(os.path.normpath(project.vfx_root)), norm_sid)
        sig = self._shot_media_dir_signature(shot_dir) if use_cache else None
        if use_cache and sig is not None:
            with self._media_cache_lock:
                cached = self._media_cache.get(cache_key)
                if cached is not None and cached[0] == sig:
                    # Sync mutable persisted color/notes & return shallow list copy
                    cached_items = cached[1]
                    for m in cached_items:
                        persisted = project.get_media_meta(m.media_key)
                        m.color = persisted.get("color", "")
                        m.notes = persisted.get("notes", "")
                    return list(cached_items)

        # Pre-index existing media thumbnail filenames in one scandir (avoids N os.path.isfile calls)
        existing_thumbs: set = set()
        media_thumb_dir = os.path.join(project.thumbnails_dir, "media", norm_sid) if project.thumbnails_dir else ""
        if media_thumb_dir:
            try:
                with os.scandir(media_thumb_dir) as t_it:
                    for t_entry in t_it:
                        try:
                            if t_entry.is_file() and t_entry.stat().st_size > 0:
                                existing_thumbs.add(os.path.normcase(os.path.normpath(t_entry.path)))
                        except OSError:
                            continue
            except OSError:
                pass

        categories = (
            ("media", os.path.join(shot_dir, "media")),
            ("assets", os.path.join(shot_dir, "assets")),
        )

        for cat_name, cat_root in categories:
            try:
                id_entries = sorted(os.scandir(cat_root), key=lambda e: e.name)
            except OSError:
                continue
            for id_entry in id_entries:
                try:
                    if not id_entry.is_dir():
                        continue
                except OSError:
                    continue
                identifier = id_entry.name
                id_path = id_entry.path
                v_dirs: List[Tuple[str, str]] = []
                try:
                    with os.scandir(id_path) as v_it:
                        for v_entry in v_it:
                            if _VERSION_DIR_RE.match(v_entry.name):
                                try:
                                    if v_entry.is_dir():
                                        v_dirs.append((v_entry.name, v_entry.path))
                                except OSError:
                                    continue
                except OSError:
                    continue

                v_dirs.sort(key=lambda x: x[0].lower(), reverse=True)
                for v_name, v_path in v_dirs:
                    info = self.inspect_media_folder(v_path)
                    if not info:
                        try:
                            sub_entries = sorted(os.scandir(v_path), key=lambda e: e.name)
                        except OSError:
                            sub_entries = []
                        for sub_entry in sub_entries:
                            try:
                                if not sub_entry.is_dir():
                                    continue
                            except OSError:
                                continue
                            info = self.inspect_media_folder(sub_entry.path)
                            if info:
                                info["aov"] = sub_entry.name
                                break
                    if info:
                        m_thumb = get_media_thumbnail_path(project, norm_sid, cat_name, identifier, v_name)
                        has_t = bool(m_thumb and os.path.normcase(os.path.normpath(m_thumb)) in existing_thumbs)
                        m_key = make_media_key(norm_sid, cat_name, identifier, v_name)
                        persisted = project.get_media_meta(m_key)
                        item = MediaItem(
                            shot_id=norm_sid,
                            category=cat_name,
                            identifier=identifier,
                            version=v_name,
                            version_dir=os.path.normpath(v_path),
                            thumb_path=m_thumb,
                            has_thumb=has_t,
                            color=persisted.get("color", ""),
                            notes=persisted.get("notes", ""),
                            **info,
                        )
                        items.append(item)

        if use_cache:
            post_sig = self._shot_media_dir_signature(shot_dir)
            with self._media_cache_lock:
                self._media_cache[cache_key] = (post_sig, list(items))
        return items

    def scan_shots_media(self, project: Project, shot_ids: List[str], use_cache: bool = True) -> List[MediaItem]:
        """Scans and aggregates MediaItem entries across multiple shots in order."""
        aggregated: List[MediaItem] = []
        if not project or not shot_ids:
            return aggregated
        seen = set()
        for sid in shot_ids:
            if not sid or sid in seen:
                continue
            seen.add(sid)
            aggregated.extend(self.scan_all_shot_media(project, sid, use_cache=use_cache))
        return aggregated

    def inspect_media_folder(self, folder_path: str) -> Optional[Dict[str, Any]]:
        """
        Inspects a directory for image sequences (first), video files, or 3D cache/asset files.
        Uses os.scandir() to avoid per-frame os.stat() syscall storms on large sequences.
        """
        try:
            with os.scandir(folder_path) as it:
                entries = list(it)
        except OSError:
            return None

        sequences: Dict[str, Dict[str, Any]] = {}
        single_entries: List[os.DirEntry] = []

        for entry in entries:
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            f = entry.name
            dot_idx = f.rfind(".")
            ext = f[dot_idx:].lower() if dot_idx != -1 else ""
            if ext not in _VALID_MEDIA_ASSET_EXTS and not f.lower().endswith(".bgeo.sc"):
                continue

            if ext in _SEQUENCE_EXTS:
                m = _SEQ_FRAME_RE.match(f)
                if m:
                    base, sep, frame_str, ext_no_dot = m.groups()
                    padding = len(frame_str)
                    key = f"{base}{sep}{'#' * padding}.{ext_no_dot}"
                    seq_data = sequences.get(key)
                    if seq_data is None:
                        seq_data = {
                            "base": base,
                            "sep": sep,
                            "padding": padding,
                            "ext": ext_no_dot,
                            "min_frame": int(frame_str),
                            "max_frame": int(frame_str),
                            "first_name": f,
                            "last_name": f,
                            "count": 0,
                            "max_mtime": 0.0,
                            "total_size": 0,
                        }
                        sequences[key] = seq_data
                    f_num = int(frame_str)
                    seq_data["count"] += 1
                    if f_num < seq_data["min_frame"]:
                        seq_data["min_frame"] = f_num
                        seq_data["first_name"] = f
                    elif f_num > seq_data["max_frame"]:
                        seq_data["max_frame"] = f_num
                        seq_data["last_name"] = f
                    # On Windows, DirEntry.stat() is free (cached in WIN32_FIND_DATA)
                    if _IS_WIN32:
                        try:
                            st = entry.stat()
                            if st.st_mtime > seq_data["max_mtime"]:
                                seq_data["max_mtime"] = st.st_mtime
                            seq_data["total_size"] += st.st_size
                        except OSError:
                            pass
                    continue
            single_entries.append(entry)

        if sequences:
            # Pick deterministically by sorted pattern key
            pattern = min(sequences.keys())
            seq_data = sequences[pattern]
            printf_pattern = f"{seq_data['base']}{seq_data['sep']}%0{seq_data['padding']}d.{seq_data['ext']}"
            first_file = os.path.join(folder_path, seq_data["first_name"])
            mtime = seq_data["max_mtime"]
            file_size = seq_data["total_size"]
            if not _IS_WIN32:
                # Avoid stat'ing every frame on POSIX/macOS: stat folder + first/last frame only
                try:
                    mtime = os.stat(folder_path).st_mtime
                except OSError:
                    mtime = 0.0
                for edge_name in {seq_data["first_name"], seq_data["last_name"]}:
                    try:
                        st = os.stat(os.path.join(folder_path, edge_name))
                        if st.st_mtime > mtime:
                            mtime = st.st_mtime
                        if file_size == 0:
                            file_size = st.st_size * seq_data["count"]
                    except OSError:
                        pass
            return {
                "is_sequence": True,
                "is_playable": True,
                "display_file": pattern,
                "nuke_pattern": os.path.join(folder_path, printf_pattern).replace("\\", "/"),
                "first_file_path": os.path.normpath(first_file),
                "folder_path": os.path.normpath(folder_path),
                "start_frame": seq_data["min_frame"],
                "end_frame": seq_data["max_frame"],
                "frame_count": seq_data["count"],
                "ext": f".{seq_data['ext'].lower()}",
                "mtime": mtime,
                "modified_at": format_mtime(mtime),
                "file_size": file_size,
            }
        elif single_entries:
            single_entries.sort(key=lambda e: e.name)
            chosen_entry = single_entries[0]
            chosen = chosen_entry.name
            ext = os.path.splitext(chosen)[1].lower()
            full_chosen = chosen_entry.path
            try:
                st = chosen_entry.stat()
                mtime = st.st_mtime
                file_size = st.st_size
            except OSError:
                mtime = 0.0
                file_size = 0
            return {
                "is_sequence": False,
                "is_playable": ext in _MEDIA_EXTS,
                "display_file": chosen,
                "nuke_pattern": full_chosen.replace("\\", "/"),
                "first_file_path": os.path.normpath(full_chosen),
                "folder_path": os.path.normpath(folder_path),
                "start_frame": DEFAULT_START_FRAME,
                "end_frame": DEFAULT_START_FRAME,
                "frame_count": 1,
                "ext": ext,
                "mtime": mtime,
                "modified_at": format_mtime(mtime),
                "file_size": file_size,
            }
        return None
