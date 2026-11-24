from typing import Any, Dict, List, Optional

from core.models.shot import Shot


class Sequence:
    """Represents a sequence (e.g. 'R01') grouping Shots and editorial timeline/EDL metadata in a Project."""

    def __init__(
        self,
        name: str,
        shots: Optional[List[Shot]] = None,
        timeline: str = "",
        edl_path: str = "",
    ):
        self.name = str(name or "").strip()
        self.timeline = str(timeline or "").strip()
        self.edl_path = str(edl_path or "").strip()
        self.shots: Dict[str, Shot] = {}
        for sh in (shots or []):
            self.add_shot(sh)

    def add_shot(self, shot: Shot) -> None:
        if not shot.shot_id:
            return
        self.shots[shot.shot_id] = shot

    def get_shot(self, shot_id: str) -> Optional[Shot]:
        return self.shots.get(shot_id)

    def remove_shot(self, shot_id: str) -> Optional[Shot]:
        return self.shots.pop(shot_id, None)

    def list_shots(self) -> List[Shot]:
        return [self.shots[k] for k in sorted(self.shots.keys())]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timeline": self.timeline,
            "edl_path": self.edl_path,
            "shots": [s.to_dict() for s in self.list_shots()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Sequence":
        shots = [Shot.from_dict(s) for s in data.get("shots", []) if isinstance(s, dict)]
        return cls(
            name=data.get("name", ""),
            shots=shots,
            timeline=data.get("timeline", ""),
            edl_path=data.get("edl_path", ""),
        )
