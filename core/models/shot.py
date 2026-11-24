import re
from typing import Any, Dict, List, Optional, Tuple

from config import DEFAULT_HANDLES, DEFAULT_START_FRAME
from core.models.resolution import format_resolution, parse_resolution
from core.timecode import format_shot_id, normalize_shot_id, split_shot_id, timecode_to_frame


class Shot:
    """Represents a VFX shot belonging to a Sequence inside a Project."""

    EXCEL_HEADERS = [
        "Shot ID", "Clip Name", "Thumbnails", "Duration", "Handles",
        "Start TC", "End TC", "Status", "Assign", "Notes",
    ]

    def __init__(
        self,
        shot_id: str = "",
        name: str = "",
        thumb: str = "",
        dur: str = "",
        start: str = "",
        end: str = "",
        status: str = "Pending",
        assign: str = "",
        notes: str = "",
        handles: str = str(DEFAULT_HANDLES),
        seq: str = "",
        shot: str = "",
        duration: str = "",
        start_tc: str = "",
        end_tc: str = "",
        start_frame: int = DEFAULT_START_FRAME,
        comp_version: str = "",
        resolution: Any = None,
        fps: float = 0.0,
        custom_fields: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        if not shot_id and (seq or shot):
            shot_id = format_shot_id(seq, shot)
        self.shot_id = normalize_shot_id(shot_id) if shot_id else ""
        if seq:
            self.seq = seq
        if shot:
            self.shot = shot
        self.name = name
        self.thumb = thumb
        self.duration = str(duration or dur) if (duration or dur) is not None else ""
        self.start_tc = str(start_tc or start or "")
        self.end_tc = str(end_tc or end or "")
        self.status = status or "Pending"
        self.assign = assign
        self.notes = notes
        self.handles = str(handles) if handles != "" else str(DEFAULT_HANDLES)
        self.start_frame = int(start_frame) if start_frame else DEFAULT_START_FRAME
        self.comp_version = comp_version or ""
        self.resolution: Optional[Tuple[float, float]] = parse_resolution(resolution)
        try:
            self.fps = float(fps) if fps else 0.0
        except (TypeError, ValueError):
            self.fps = 0.0
        self.custom_fields: Dict[str, str] = {
            str(k): str(v) for k, v in (custom_fields or {}).items() if str(k).strip()
        }

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
    def handles_int(self) -> int:
        try:
            return max(0, int(float(self.handles)))
        except ValueError:
            return DEFAULT_HANDLES

    @property
    def duration_int(self) -> int:
        if self.duration:
            try:
                m = re.search(r"\d+", str(self.duration))
                if m:
                    val = int(m.group())
                    if val > 0:
                        return val
            except Exception:
                pass
        if self.start_tc and self.end_tc:
            f_start = timecode_to_frame(self.start_tc, 24.0)
            f_end = timecode_to_frame(self.end_tc, 24.0)
            if f_end > f_start:
                return f_end - f_start
        return 0

    @property
    def end_frame(self) -> int:
        dur = self.duration_int or 100
        return self.start_frame + dur - 1

    @property
    def resolution_str(self) -> str:
        return format_resolution(self.resolution)

    def get_field(self, name: str, default: str = "") -> str:
        """Uniform getter for both intrinsic Shot attributes and project-defined custom fields."""
        if not name:
            return default
        low = name.strip().lower()
        if low == "status":
            return self.status or default
        if low in ("assign", "artist"):
            return self.assign or default
        if low == "resolution":
            return self.resolution_str or default
        if name in self.custom_fields:
            return self.custom_fields[name]
        if hasattr(self, name) and not name.startswith("_"):
            val = getattr(self, name)
            return "" if val is None else str(val)
        return default

    def set_field(self, name: str, value: Any) -> None:
        """Uniform setter for both intrinsic Shot attributes and project-defined custom fields."""
        if not name:
            return
        low = name.strip().lower()
        if low == "status":
            self.status = str(value or "").strip() or "Pending"
            return
        if low in ("assign", "artist"):
            self.assign = str(value or "").strip()
            return
        if low == "resolution":
            self.resolution = parse_resolution(value)
            return
        intrinsic_str = {"name", "thumb", "duration", "start_tc", "end_tc", "notes", "handles", "comp_version"}
        if name in intrinsic_str:
            setattr(self, name, str(value or "").strip())
        elif name == "start_frame":
            self.start_frame = int(value) if value else DEFAULT_START_FRAME
        elif name == "fps":
            self.fps = float(value) if value else 0.0
        else:
            val_str = str(value or "").strip()
            if val_str:
                self.custom_fields[name] = val_str
            else:
                self.custom_fields.pop(name, None)

    def matches_filter(
        self,
        seq_filter: str = "All Sequences",
        status_filter: str = "All Statuses",
        search_text: str = "",
        artist_filter: str = "All Artists",
    ) -> bool:
        """Checks if the shot matches the current UI filters and search query."""
        if seq_filter and seq_filter != "All Sequences" and self.seq != seq_filter:
            return False
        if status_filter and status_filter != "All Statuses" and (self.status or "Pending").lower() != status_filter.lower():
            return False
        if artist_filter and artist_filter != "All Artists":
            assigned_list = [a.strip().lower() for a in (self.assign or "").split(",") if a.strip()]
            if artist_filter.strip().lower() not in assigned_list:
                return False
        if search_text:
            q = search_text.lower().strip()
            custom_vals = " ".join(str(v) for v in self.custom_fields.values())
            haystack = f"{self.shot_id} {self.name} {self.status} {self.assign} {self.notes} {custom_vals}".lower()
            if q not in haystack:
                return False
        return True

    def to_list(self) -> List[str]:
        return [
            self.shot_id,
            self.name,
            self.thumb,
            self.duration,
            self.start_tc,
            self.end_tc,
            self.status,
            self.assign,
            self.notes,
            self.handles,
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "name": self.name,
            "thumb": self.thumb,
            "duration": self.duration,
            "start_tc": self.start_tc,
            "end_tc": self.end_tc,
            "status": self.status,
            "assign": self.assign,
            "notes": self.notes,
            "handles": self.handles,
            "start_frame": self.start_frame,
            "comp_version": self.comp_version,
            "resolution": [float(self.resolution[0]), float(self.resolution[1])] if self.resolution else None,
            "fps": self.fps,
            "custom_fields": dict(self.custom_fields),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Shot":
        raw_cf = d.get("custom_fields")
        custom_fields = dict(raw_cf) if isinstance(raw_cf, dict) else {}
        return cls(
            shot_id=d.get("shot_id") or d.get("Shot ID") or "",
            name=d.get("name") or d.get("Clip Name") or "",
            thumb=d.get("thumb") or d.get("Thumbnails") or "",
            dur=d.get("duration") or d.get("Duration") or "",
            start=d.get("start_tc") or d.get("Start TC") or "",
            end=d.get("end_tc") or d.get("End TC") or "",
            status=d.get("status") or d.get("Status") or "Pending",
            assign=d.get("assign") or d.get("Assign") or "",
            notes=d.get("notes") or d.get("Notes") or "",
            handles=str(d.get("handles") or d.get("Handles") or DEFAULT_HANDLES),
            start_frame=int(d.get("start_frame") or DEFAULT_START_FRAME),
            comp_version=str(d.get("comp_version") or ""),
            resolution=d.get("resolution") if d.get("resolution") is not None else d.get("Resolution"),
            fps=float(d.get("fps") or d.get("FPS") or 0.0),
            custom_fields=custom_fields,
        )

    @classmethod
    def from_list(cls, row: List[str]) -> "Shot":
        padded = list(row)
        while len(padded) < 10:
            padded.append(str(DEFAULT_HANDLES) if len(padded) == 9 else "")
        return cls(
            shot_id=padded[0],
            name=padded[1],
            thumb=padded[2],
            dur=padded[3],
            start=padded[4],
            end=padded[5],
            status=padded[6],
            assign=padded[7],
            notes=padded[8],
            handles=padded[9],
        )
