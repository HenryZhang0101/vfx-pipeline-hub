"""Domain model representing a versioned media or asset item inside a Shot."""

import os
import re
from datetime import datetime
from typing import Any, Dict, Iterator, Optional, Tuple

from config import DEFAULT_START_FRAME
from core.timecode import format_shot_id, split_shot_id


def make_media_key(shot_id: str, category: str, identifier: str, version: str) -> str:
    """Canonical, OS-independent key identifying a media/asset version within a Project."""
    sid = (shot_id or "").strip()
    cat = (category or "media").strip().lower()
    ident = (identifier or "").strip()
    ver = (version or "v0001").strip().lower()
    return f"{sid}|{cat}|{ident}|{ver}"


def format_mtime(mtime: float) -> str:
    """Format a POSIX timestamp as 'YYYY-MM-DD HH:MM' (empty string if invalid/zero)."""
    if not mtime or mtime <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(mtime)).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


class MediaItem:
    """
    Represents a versioned media or 3D asset entry belonging to a Shot
    (<vfx_root>/<SEQ>/<SHOT>/<category>/<identifier>/<version>/).

    Mirrors the Shot domain model while providing lightweight dict-like access
    (get, __getitem__, __setitem__, __contains__) for backwards compatibility
    with existing callers.
    """

    def __init__(
        self,
        shot_id: str = "",
        seq: str = "",
        shot: str = "",
        category: str = "media",
        identifier: str = "plate",
        version: str = "v0001",
        version_dir: str = "",
        folder_path: str = "",
        first_file_path: str = "",
        display_file: str = "",
        nuke_pattern: str = "",
        ext: str = "",
        is_sequence: bool = False,
        is_playable: bool = False,
        start_frame: int = DEFAULT_START_FRAME,
        end_frame: int = DEFAULT_START_FRAME,
        frame_count: int = 1,
        aov: str = "",
        thumb_path: str = "",
        has_thumb: Optional[bool] = None,
        mtime: float = 0.0,
        modified_at: str = "",
        file_size: int = 0,
        color: str = "",
        notes: str = "",
        **kwargs,
    ):
        if not shot_id and (seq or shot):
            shot_id = format_shot_id(seq, shot)
        self.shot_id = (shot_id or "").strip()
        if seq and not self.shot_id:
            self.seq = seq
        if shot and not self.shot_id:
            self.shot = shot

        self.category = (category or "media").strip().lower()
        self.identifier = (identifier or "").strip()
        self.version = (version or "v0001").strip()
        self.version_dir = os.path.normpath(version_dir) if version_dir else ""
        self.folder_path = os.path.normpath(folder_path) if folder_path else self.version_dir
        self.first_file_path = os.path.normpath(first_file_path) if first_file_path else ""
        self.display_file = display_file or ""
        self.nuke_pattern = nuke_pattern or ""
        self.ext = (ext or "").lower()
        self.is_sequence = bool(is_sequence)
        self.is_playable = bool(is_playable)
        self.start_frame = int(start_frame) if start_frame is not None else DEFAULT_START_FRAME
        self.end_frame = int(end_frame) if end_frame is not None else self.start_frame
        self.frame_count = max(1, int(frame_count)) if frame_count else 1
        self.aov = (aov or "").strip()
        self.thumb_path = thumb_path or ""
        if has_thumb is None:
            self.has_thumb = bool(self.thumb_path and os.path.isfile(self.thumb_path))
        else:
            self.has_thumb = bool(has_thumb)

        try:
            self.mtime = float(mtime) if mtime else 0.0
        except (TypeError, ValueError):
            self.mtime = 0.0
        self.modified_at = (modified_at or "").strip() or format_mtime(self.mtime)
        try:
            self.file_size = max(0, int(file_size)) if file_size else 0
        except (TypeError, ValueError):
            self.file_size = 0

        self.color = (color or "").strip()
        self.notes = (notes or "").strip()

    @property
    def seq(self) -> str:
        s, _ = split_shot_id(self.shot_id)
        return s

    @seq.setter
    def seq(self, val: str) -> None:
        _, sh = split_shot_id(self.shot_id)
        self.shot_id = format_shot_id(val, sh)

    @property
    def shot(self) -> str:
        _, sh = split_shot_id(self.shot_id)
        return sh

    @shot.setter
    def shot(self, val: str) -> None:
        s, _ = split_shot_id(self.shot_id)
        self.shot_id = format_shot_id(s, val)

    @property
    def media_key(self) -> str:
        return make_media_key(self.shot_id, self.category, self.identifier, self.version)

    @property
    def group_key(self) -> Tuple[str, str, str]:
        """Grouping key for identifying the latest version per (shot, category, identifier)."""
        return (self.shot_id.lower(), self.category.lower(), self.identifier.lower())

    @property
    def version_num(self) -> int:
        m = re.search(r"(\d+)", self.version or "")
        return int(m.group(1)) if m else 0

    @property
    def label(self) -> str:
        return f"[{self.category}] {self.identifier} / {self.version}"

    @property
    def range_display(self) -> str:
        if self.is_sequence:
            return f"{self.start_frame}–{self.end_frame} ({self.frame_count}f)"
        return "Single File"

    def matches_filter(
        self,
        category_filter: str = "all",
        identifier_filter: str = "all",
        color_filter: str = "all",
        search_text: str = "",
    ) -> bool:
        """Checks if this media item matches the MediaManagerPanel filters and search query."""
        cat = (category_filter or "all").strip().lower()
        if cat and cat != "all" and self.category.lower() != cat:
            return False

        ident = (identifier_filter or "all").strip().lower()
        if ident and ident != "all" and self.identifier.lower() != ident:
            return False

        col = (color_filter or "all").strip().lower()
        if col and col != "all":
            if col == "none":
                if self.color:
                    return False
            elif self.color.lower() != col:
                return False

        if search_text:
            q = search_text.lower().strip()
            haystack = (
                f"{self.shot_id} {self.category} {self.identifier} "
                f"{self.version} {self.display_file} {self.ext} {self.aov} {self.notes}"
            ).lower()
            if q not in haystack:
                return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "shot": self.shot,
            "shot_id": self.shot_id,
            "category": self.category,
            "identifier": self.identifier,
            "version": self.version,
            "version_dir": self.version_dir,
            "folder_path": self.folder_path,
            "first_file_path": self.first_file_path,
            "display_file": self.display_file,
            "nuke_pattern": self.nuke_pattern,
            "ext": self.ext,
            "is_sequence": self.is_sequence,
            "is_playable": self.is_playable,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frame_count": self.frame_count,
            "aov": self.aov,
            "label": self.label,
            "thumb_path": self.thumb_path,
            "has_thumb": self.has_thumb,
            "mtime": self.mtime,
            "modified_at": self.modified_at,
            "file_size": self.file_size,
            "color": self.color,
            "notes": self.notes,
            "media_key": self.media_key,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MediaItem":
        if isinstance(d, cls):
            return d
        return cls(**d)

    # Lightweight dict-compatibility protocol for legacy callers (e.g. thumbnails, controllers, Resolve UI)
    _DICT_KEYS = (
        "seq",
        "shot",
        "shot_id",
        "category",
        "identifier",
        "version",
        "version_dir",
        "folder_path",
        "first_file_path",
        "display_file",
        "nuke_pattern",
        "ext",
        "is_sequence",
        "is_playable",
        "start_frame",
        "end_frame",
        "frame_count",
        "aov",
        "label",
        "thumb_path",
        "has_thumb",
        "mtime",
        "modified_at",
        "file_size",
        "color",
        "notes",
        "media_key",
    )

    def __getitem__(self, key: str) -> Any:
        if key in self._DICT_KEYS:
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._DICT_KEYS and key not in ("label", "media_key"):
            setattr(self, key, value)
        else:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._DICT_KEYS:
            return getattr(self, key)
        return default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._DICT_KEYS

    def keys(self):
        return self._DICT_KEYS

    def items(self):
        return self.to_dict().items()

    def __iter__(self) -> Iterator[str]:
        return iter(self._DICT_KEYS)

    def __repr__(self) -> str:
        return f"MediaItem({self.shot_id!r}, {self.category!r}, {self.identifier!r}, {self.version!r})"
