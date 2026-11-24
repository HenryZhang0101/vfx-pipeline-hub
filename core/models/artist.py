from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.models.property import pick_palette_color


def default_artist_color(name: str) -> str:
    """Deterministic fallback badge color for an artist name."""
    if not name:
        return "#5A5A58"
    return pick_palette_color(sum(ord(ch) for ch in name))


@dataclass
class Artist:
    """Represents a VFX artist in a project."""

    name: str
    role: str = ""
    departments: List[str] = field(default_factory=list)
    email: str = ""
    active: bool = True
    color: str = ""

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        self.color = str(self.color or "").strip() or default_artist_color(self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "departments": list(self.departments),
            "email": self.email,
            "active": self.active,
            "color": self.color or default_artist_color(self.name),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artist":
        name = str(data.get("name", "")).strip()
        return cls(
            name=name,
            role=str(data.get("role", "")).strip(),
            departments=[str(d) for d in data.get("departments", []) if d],
            email=str(data.get("email", "")).strip(),
            active=bool(data.get("active", True)),
            color=str(data.get("color", "")).strip() or default_artist_color(name),
        )
