import os
import re
from typing import Any, Dict, List, Optional, Tuple

from config import DEFAULT_EXPORT_FOLDER, DEFAULT_HANDLES, DEFAULT_START_FRAME, FOLDER_SHOT_SESSIONS, get_ocio_config
from core.models.artist import Artist, default_artist_color
from core.models.property import (
    PropertyType,
    ShotProperty,
    default_artist_property,
    default_status_property,
    slugify_property_key,
)
from core.models.resolution import (
    ResolutionPreset,
    default_resolution_presets,
    format_resolution,
    parse_resolution,
)
from core.models.sequence import Sequence
from core.models.shot import Shot
from core.timecode import normalize_shot_id, split_shot_id


def infer_project_name_from_path(root_dir: str) -> str:
    """Infer a sensible default project name from a VFX root path (e.g. H:/MyShow/05_vfx -> MyShow)."""
    if not root_dir:
        return "VFX_Project"
    norm = os.path.normpath(root_dir).rstrip("\\/")
    base = os.path.basename(norm)
    if re.match(r"^(\d+[_\s-]*)?(prism[_\s-]*)?vfx$", base, re.IGNORECASE):
        parent_base = os.path.basename(os.path.dirname(norm))
        if parent_base and not parent_base.endswith(":"):
            return parent_base
    return base or "VFX_Project"


class Project:
    """Top-level VFX project aggregate owning sequences, shots, artists, and project settings."""

    def __init__(
        self,
        root_dir: str = "",
        name: str = "",
        fps: float = 24.0,
        resolution: Tuple[float, float] = (1920.0, 1080.0),
        default_start_frame: int = DEFAULT_START_FRAME,
        default_handles: int = DEFAULT_HANDLES,
        export_folder: str = DEFAULT_EXPORT_FOLDER,
        ocio_config: str = "",
        sequences: Optional[Dict[str, Sequence]] = None,
        artists: Optional[Dict[str, Artist]] = None,
        google_sheet_url: str = "",
        last_sync_time: str = "",
        last_modified: str = "",
        comp_versions: Optional[Dict[str, Any]] = None,
        shot_custom_fields: Optional[List[str]] = None,
        shot_properties: Optional[List[ShotProperty]] = None,
        resolution_presets: Optional[List[ResolutionPreset]] = None,
        media_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
        ignored_shots: Optional[List[str]] = None,
    ):
        self.root_dir = os.path.normpath(root_dir) if root_dir else ""
        self.name = name or infer_project_name_from_path(self.root_dir)
        self.fps = float(fps) if fps else 24.0
        self.resolution: Tuple[float, float] = parse_resolution(resolution) or (1920.0, 1080.0)
        if resolution_presets is not None:
            parsed_presets: List[ResolutionPreset] = []
            for item in resolution_presets:
                if isinstance(item, ResolutionPreset):
                    parsed_presets.append(item)
                elif isinstance(item, dict):
                    pr = ResolutionPreset.from_dict(item)
                    if pr:
                        parsed_presets.append(pr)
            self.resolution_presets: List[ResolutionPreset] = parsed_presets or default_resolution_presets()
        else:
            self.resolution_presets = default_resolution_presets()
        self.default_start_frame = int(default_start_frame) if default_start_frame is not None else DEFAULT_START_FRAME
        self.default_handles = int(default_handles) if default_handles is not None else DEFAULT_HANDLES
        self.export_folder: str = (export_folder or DEFAULT_EXPORT_FOLDER).strip() or DEFAULT_EXPORT_FOLDER
        self.ocio_config: str = (ocio_config or "").strip()
        self.sequences: Dict[str, Sequence] = sequences or {}
        self.artists: Dict[str, Artist] = artists or {}
        self.google_sheet_url = google_sheet_url
        self.last_sync_time = last_sync_time
        self.last_modified = last_modified
        self.comp_versions: Dict[str, Any] = comp_versions or {}
        self.ignored_shots: List[str] = sorted(
            {str(s).strip() for s in (ignored_shots or []) if str(s or "").strip()}
        )
        self.media_metadata: Dict[str, Dict[str, Any]] = {}
        if isinstance(media_metadata, dict):
            for mk, mv in media_metadata.items():
                if isinstance(mv, dict):
                    clean_entry: Dict[str, Any] = {}
                    col = str(mv.get("color") or "").strip()
                    nt = str(mv.get("notes") or "").strip()
                    if col:
                        clean_entry["color"] = col
                    if nt:
                        clean_entry["notes"] = nt
                    if clean_entry:
                        self.media_metadata[str(mk)] = clean_entry
        self.shot_properties: List[ShotProperty] = []
        if shot_properties:
            self.shot_properties = [
                p if isinstance(p, ShotProperty) else ShotProperty.from_dict(p)
                for p in shot_properties
            ]
        self._ensure_builtin_properties()
        if shot_custom_fields and not shot_properties:
            for fname in shot_custom_fields:
                self.add_custom_field(fname)

    @property
    def vfx_root(self) -> str:
        return self.root_dir

    @property
    def resolution_str(self) -> str:
        return format_resolution(self.resolution)

    @property
    def exports_dir(self) -> str:
        if not self.vfx_root:
            return ""
        folder = (self.export_folder or DEFAULT_EXPORT_FOLDER).strip() or DEFAULT_EXPORT_FOLDER
        if os.path.isabs(folder):
            return os.path.normpath(folder)
        return os.path.join(self.vfx_root, folder)

    @property
    def effective_ocio_config(self) -> str:
        """Resolved OCIO config path for this project (project override -> global -> bundled default)."""
        return get_ocio_config(self.ocio_config)

    @property
    def thumbnails_dir(self) -> str:
        return os.path.join(self.vfx_root, "_thumbnails") if self.vfx_root else ""

    @property
    def project_json_path(self) -> str:
        return os.path.join(self.vfx_root, "project.json") if self.vfx_root else ""

    @property
    def default_excel_path(self) -> str:
        return os.path.join(self.vfx_root, f"{self.name}_ShotList.xlsx") if self.vfx_root else ""

    @property
    def sessions_dir(self) -> str:
        return os.path.join(self.root_dir, FOLDER_SHOT_SESSIONS) if self.root_dir else ""

    @property
    def legacy_csv_path(self) -> str:
        return os.path.join(self.sessions_dir, f"{self.name}_shots.csv") if self.sessions_dir else ""

    def get_or_create_sequence(self, seq_name: str) -> Sequence:
        seq_name = (seq_name or "R01").strip()
        if seq_name not in self.sequences:
            self.sequences[seq_name] = Sequence(seq_name)
        return self.sequences[seq_name]

    def add_shot(self, shot: Shot) -> None:
        self.unignore_shot(shot.shot_id)
        seq_name, _ = split_shot_id(shot.shot_id)
        seq = self.get_or_create_sequence(seq_name)
        seq.add_shot(shot)

    def ignore_shot(self, shot_id: str) -> None:
        sid = normalize_shot_id(shot_id)
        if sid and sid not in self.ignored_shots:
            self.ignored_shots.append(sid)
            self.ignored_shots.sort()

    def unignore_shot(self, shot_id: str) -> None:
        raw = (shot_id or "").strip()
        sid = normalize_shot_id(shot_id)
        if raw or sid:
            self.ignored_shots = [s for s in self.ignored_shots if s not in (raw, sid)]

    def is_shot_ignored(self, shot_id: str) -> bool:
        raw = (shot_id or "").strip()
        sid = normalize_shot_id(shot_id)
        return bool((raw and raw in self.ignored_shots) or (sid and sid in self.ignored_shots))

    def get_shot(self, shot_id: str) -> Optional[Shot]:
        norm_id = normalize_shot_id(shot_id)
        seq_name, _ = split_shot_id(norm_id or shot_id)
        seq = self.sequences.get(seq_name)
        if seq:
            found = seq.get_shot(norm_id) or seq.get_shot(shot_id)
            if found:
                return found
        for s in self.sequences.values():
            found = s.get_shot(norm_id) or s.get_shot(shot_id)
            if found:
                return found
        return None

    def remove_shot(self, shot_id: str) -> Optional[Shot]:
        norm_id = normalize_shot_id(shot_id)
        seq_name, _ = split_shot_id(norm_id or shot_id)
        seq = self.sequences.get(seq_name)
        if seq:
            removed = seq.remove_shot(norm_id) or seq.remove_shot(shot_id)
            if removed:
                return removed
        for s in self.sequences.values():
            removed = s.remove_shot(norm_id) or s.remove_shot(shot_id)
            if removed:
                return removed
        return None

    def all_shots(self) -> List[Shot]:
        shots: List[Shot] = []
        for seq_key in sorted(self.sequences.keys()):
            shots.extend(self.sequences[seq_key].list_shots())
        return shots

    def replace_shots(self, shots: List[Shot]) -> None:
        self.sequences.clear()
        for shot in shots:
            self.add_shot(shot)

    def add_artist(self, artist: Artist) -> None:
        if artist.name:
            self.artists[artist.name] = artist

    def remove_artist(self, name: str) -> None:
        if name in self.artists:
            del self.artists[name]

    def get_artist(self, name: str) -> Optional[Artist]:
        return self.artists.get(name)

    def list_artists(self, active_only: bool = False) -> List[Artist]:
        items = [self.artists[k] for k in sorted(self.artists.keys())]
        if active_only:
            items = [a for a in items if a.active]
        return items

    def _ensure_builtin_properties(self) -> None:
        """Guarantees built-in Status ('status') and Artist ('assign') properties exist."""
        has_status = any(p.key == "status" for p in self.shot_properties)
        has_artist = any(p.key == "assign" for p in self.shot_properties)
        if not has_artist:
            self.shot_properties.insert(0, default_artist_property())
        if not has_status:
            self.shot_properties.insert(0, default_status_property())
        for p in self.shot_properties:
            if p.key in ("status", "assign"):
                p.builtin = True
            if p.key == "status" and not p.options:
                p.options = default_status_property().options

    @property
    def shot_custom_fields(self) -> List[str]:
        """Backwards-compatible ordered list of custom (non-builtin) property names."""
        return [p.name for p in self.shot_properties if not p.builtin and p.name]

    @shot_custom_fields.setter
    def shot_custom_fields(self, names: List[str]) -> None:
        builtins = [p for p in self.shot_properties if p.builtin]
        existing_custom = {p.name.lower(): p for p in self.shot_properties if not p.builtin}
        new_custom: List[ShotProperty] = []
        seen = set()
        for raw_name in names or []:
            nm = str(raw_name or "").strip()
            if not nm or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            if nm.lower() in existing_custom:
                new_custom.append(existing_custom[nm.lower()])
            else:
                new_custom.append(
                    ShotProperty(
                        key=slugify_property_key(nm),
                        name=nm,
                        prop_type=PropertyType.TEXT,
                    )
                )
        self.shot_properties = builtins + new_custom
        self._ensure_builtin_properties()

    def get_property(self, key_or_name: str) -> Optional[ShotProperty]:
        target = (key_or_name or "").strip().lower()
        if not target:
            return None
        for p in self.shot_properties:
            if p.key.lower() == target or p.name.lower() == target:
                return p
        return None

    def get_status_property(self) -> ShotProperty:
        prop = self.get_property("status")
        if prop is None:
            self._ensure_builtin_properties()
            prop = self.get_property("status")
        return prop  # type: ignore[return-value]

    def get_artist_property(self) -> ShotProperty:
        prop = self.get_property("assign")
        if prop is None:
            self._ensure_builtin_properties()
            prop = self.get_property("assign")
        return prop  # type: ignore[return-value]

    def get_artist_color(self, name: str) -> str:
        name_clean = (name or "").strip()
        if not name_clean:
            return "#5A5A58"
        art = self.artists.get(name_clean)
        if art is None:
            low = name_clean.lower()
            for a in self.artists.values():
                if a.name.lower() == low:
                    art = a
                    break
        return art.color if (art and art.color) else default_artist_color(name_clean)

    def add_property(self, prop: ShotProperty) -> None:
        if not prop or not prop.name:
            return
        if self.get_property(prop.name) or self.get_property(prop.key):
            return
        self.shot_properties.append(prop)

    def remove_property(self, key_or_name: str) -> bool:
        prop = self.get_property(key_or_name)
        if prop is None or prop.builtin:
            return False
        self.shot_properties.remove(prop)
        return True

    def add_custom_field(self, field_name: str) -> None:
        field_name = (field_name or "").strip()
        if field_name and not self.get_property(field_name):
            self.shot_properties.append(
                ShotProperty(
                    key=slugify_property_key(field_name),
                    name=field_name,
                    prop_type=PropertyType.TEXT,
                )
            )

    def remove_custom_field(self, field_name: str) -> None:
        self.remove_property(field_name)

    def get_media_meta(self, media_key: str) -> Dict[str, Any]:
        """Returns persisted user metadata (e.g. color, notes) for a MediaItem key."""
        if not media_key:
            return {}
        return dict(self.media_metadata.get(media_key, {}))

    def set_media_meta(
        self,
        media_key: str,
        color: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Sparsely updates persisted metadata for a MediaItem; prunes empty entries."""
        if not media_key:
            return
        entry = dict(self.media_metadata.get(media_key, {}))
        if color is not None:
            col = str(color).strip()
            if col:
                entry["color"] = col
            else:
                entry.pop("color", None)
        if notes is not None:
            nt = str(notes).strip()
            if nt:
                entry["notes"] = nt
            else:
                entry.pop("notes", None)
        if entry:
            self.media_metadata[media_key] = entry
        else:
            self.media_metadata.pop(media_key, None)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "name": self.name,
            "root_dir": self.root_dir,
            "fps": self.fps,
            "resolution": [float(self.resolution[0]), float(self.resolution[1])],
            "resolution_presets": [p.to_dict() for p in self.resolution_presets],
            "default_start_frame": self.default_start_frame,
            "default_handles": self.default_handles,
            "export_folder": self.export_folder,
            "ocio_config": self.ocio_config,
            "google_sheet_url": self.google_sheet_url,
            "last_sync_time": self.last_sync_time,
            "last_modified": self.last_modified,
            "comp_versions": self.comp_versions,
            "shot_custom_fields": list(self.shot_custom_fields),
            "shot_properties": [p.to_dict() for p in self.shot_properties],
            "artists": [a.to_dict() for a in self.list_artists()],
            "sequences": {k: self.sequences[k].to_dict() for k in sorted(self.sequences.keys())},
        }
        if self.media_metadata:
            data["media_metadata"] = {
                k: dict(self.media_metadata[k]) for k in sorted(self.media_metadata.keys())
            }
        if self.ignored_shots:
            data["ignored_shots"] = sorted(set(self.ignored_shots))
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any], root_dir: str = "") -> "Project":
        resolution = parse_resolution(data.get("resolution")) or (1920.0, 1080.0)
        raw_res_presets = data.get("resolution_presets")
        resolution_presets: Optional[List[ResolutionPreset]] = None
        if isinstance(raw_res_presets, list) and raw_res_presets:
            parsed_rp = [ResolutionPreset.from_dict(r) for r in raw_res_presets if isinstance(r, dict)]
            resolution_presets = [r for r in parsed_rp if r is not None] or None

        sequences: Dict[str, Sequence] = {}
        raw_seqs = data.get("sequences", {})
        if isinstance(raw_seqs, dict):
            for seq_name, seq_data in raw_seqs.items():
                if isinstance(seq_data, dict):
                    seq = Sequence.from_dict(seq_data)
                    if not seq.name:
                        seq.name = seq_name
                    sequences[seq.name] = seq

        artists: Dict[str, Artist] = {}
        for a_data in data.get("artists", []):
            if isinstance(a_data, dict):
                artist = Artist.from_dict(a_data)
                if artist.name:
                    artists[artist.name] = artist

        raw_props = data.get("shot_properties")
        shot_properties: Optional[List[ShotProperty]] = None
        if isinstance(raw_props, list) and raw_props:
            shot_properties = [ShotProperty.from_dict(p) for p in raw_props if isinstance(p, dict)]

        raw_comp_versions = data.get("comp_versions", {})
        comp_versions: Dict[str, Any] = {}
        if isinstance(raw_comp_versions, dict):
            for k, v in raw_comp_versions.items():
                nk = normalize_shot_id(str(k))
                if nk:
                    comp_versions[nk] = dict(v) if isinstance(v, dict) else v

        raw_media_meta = data.get("media_metadata")
        media_metadata: Optional[Dict[str, Dict[str, Any]]] = None
        if isinstance(raw_media_meta, dict):
            media_metadata = {}
            for mk, mv in raw_media_meta.items():
                if not isinstance(mv, dict):
                    continue
                if "|" in str(mk):
                    sid_part, rest = str(mk).split("|", 1)
                    norm_mk = f"{normalize_shot_id(sid_part)}|{rest}"
                else:
                    norm_mk = str(mk)
                media_metadata[norm_mk] = dict(mv)

        raw_ignored = data.get("ignored_shots")
        ignored_shots: Optional[List[str]] = None
        if isinstance(raw_ignored, list):
            ignored_shots = sorted({normalize_shot_id(str(s)) for s in raw_ignored if str(s).strip()})

        return cls(
            root_dir=root_dir or data.get("root_dir", ""),
            name=data.get("name", ""),
            fps=float(data.get("fps", 24.0)),
            resolution=resolution,
            default_start_frame=int(data.get("default_start_frame", DEFAULT_START_FRAME)),
            default_handles=int(data.get("default_handles", DEFAULT_HANDLES)),
            export_folder=str(data.get("export_folder") or DEFAULT_EXPORT_FOLDER),
            ocio_config=str(data.get("ocio_config") or ""),
            sequences=sequences,
            artists=artists,
            google_sheet_url=data.get("google_sheet_url", ""),
            last_sync_time=data.get("last_sync_time", ""),
            last_modified=data.get("last_modified", ""),
            comp_versions=comp_versions,
            shot_custom_fields=data.get("shot_custom_fields", []) if isinstance(data.get("shot_custom_fields"), list) else [],
            shot_properties=shot_properties,
            resolution_presets=resolution_presets,
            media_metadata=media_metadata,
            ignored_shots=ignored_shots,
        )
