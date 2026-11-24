"""Resolution float-pair helpers and per-project ResolutionPreset model."""

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Tuple

from config import DEFAULT_RESOLUTION_PRESETS


def parse_resolution(value: Any) -> Optional[Tuple[float, float]]:
    """
    Parse a resolution value into a validated (width: float, height: float) tuple.
    Accepts:
      - (w, h) or [w, h] numeric pairs
      - {"width": w, "height": h} dicts
      - Legacy strings like "1920x1080", "2048.5 x 1080", or "1920x1080 (HD)"
    Returns None when empty or invalid.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            w, h = float(value[0]), float(value[1])
            if w > 0.0 and h > 0.0:
                return (round(w, 4), round(h, 4))
        except (TypeError, ValueError):
            return None
        return None
    if isinstance(value, dict):
        if "width" in value and "height" in value:
            return parse_resolution((value["width"], value["height"]))
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", raw)
        if m:
            try:
                w, h = float(m.group(1)), float(m.group(2))
                if w > 0.0 and h > 0.0:
                    return (round(w, 4), round(h, 4))
            except ValueError:
                return None
    return None


def _fmt_dim(val: float) -> str:
    v = round(float(val), 4)
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def format_resolution(res: Optional[Tuple[float, float]]) -> str:
    """Format a (width, height) float pair cleanly as '1920x1080' or '2048.5x1080'."""
    parsed = parse_resolution(res)
    if not parsed:
        return ""
    return f"{_fmt_dim(parsed[0])}x{_fmt_dim(parsed[1])}"


@dataclass
class ResolutionPreset:
    """User-configurable resolution preset stored per project."""

    name: str
    width: float
    height: float

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip() or "Custom"
        self.width = round(max(1.0, float(self.width)), 4)
        self.height = round(max(1.0, float(self.height)), 4)

    @property
    def dims(self) -> Tuple[float, float]:
        return (self.width, self.height)

    @property
    def display_label(self) -> str:
        return f"{self.name} ({format_resolution(self.dims)})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["ResolutionPreset"]:
        if not isinstance(data, dict):
            return None
        parsed = parse_resolution((data.get("width"), data.get("height")))
        if not parsed:
            return None
        name = str(data.get("name", "")).strip() or format_resolution(parsed)
        return cls(name=name, width=parsed[0], height=parsed[1])


def default_resolution_presets() -> List[ResolutionPreset]:
    """Return a fresh list of default industry resolution presets."""
    return [ResolutionPreset(name=n, width=w, height=h) for n, w, h in DEFAULT_RESOLUTION_PRESETS]
