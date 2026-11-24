"""Typed Notion-style Shot Property schema definitions, options, and color presets."""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Tuple


class PropertyType:
    """Supported ShotProperty data types."""

    STATUS = "status"
    PEOPLE = "people"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    DATE = "date"
    TEXT = "text"
    NUMBER = "number"
    CHECKBOX = "checkbox"

    ALL: Tuple[str, ...] = (
        STATUS,
        PEOPLE,
        SELECT,
        MULTI_SELECT,
        DATE,
        TEXT,
        NUMBER,
        CHECKBOX,
    )

    # Types a user can pick when creating a new custom property
    CUSTOM_TYPES: Tuple[str, ...] = (
        SELECT,
        MULTI_SELECT,
        PEOPLE,
        DATE,
        TEXT,
        NUMBER,
        CHECKBOX,
        STATUS,
    )

    LABELS: Dict[str, str] = {
        STATUS: "Status",
        PEOPLE: "People",
        SELECT: "Select",
        MULTI_SELECT: "Multi-Select",
        DATE: "Date",
        TEXT: "Text",
        NUMBER: "Number",
        CHECKBOX: "Checkbox",
    }

    OPTION_BASED: Tuple[str, ...] = (STATUS, SELECT, MULTI_SELECT)

    @classmethod
    def label(cls, prop_type: str) -> str:
        return cls.LABELS.get(prop_type, prop_type.title() if prop_type else "Text")

    @classmethod
    def normalize(cls, prop_type: str) -> str:
        val = str(prop_type or "").strip().lower()
        return val if val in cls.ALL else cls.TEXT


# Curated Notion-Dark pill color presets: (Display Name, Hex)
PROPERTY_COLOR_PALETTE: List[Tuple[str, str]] = [
    ("Gray", "#5A5A58"),
    ("Brown", "#7A5C49"),
    ("Orange", "#C26D2D"),
    ("Yellow", "#B88B28"),
    ("Green", "#2E7D5B"),
    ("Teal", "#3A8E82"),
    ("Blue", "#2383E2"),
    ("Purple", "#8256A3"),
    ("Pink", "#B84B84"),
    ("Red", "#C24E2D"),
]

DEFAULT_OPTION_COLOR = "#5A5A58"


def pick_palette_color(index: int) -> str:
    """Deterministic Notion-Dark palette color by index (skipping Gray for variety when possible)."""
    vivid = PROPERTY_COLOR_PALETTE[1:]  # skip Gray for auto-assigned tags/artists
    if not vivid:
        return DEFAULT_OPTION_COLOR
    return vivid[abs(int(index)) % len(vivid)][1]


def slugify_property_key(name: str) -> str:
    """Convert a property display name into a clean lowercase key."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return cleaned or "custom_field"


@dataclass
class PropertyOption:
    """A single selectable option (with badge color) for status/select/multi_select properties."""

    name: str
    color: str = DEFAULT_OPTION_COLOR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color or DEFAULT_OPTION_COLOR,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PropertyOption":
        if isinstance(data, str):
            return cls(name=data.strip(), color=DEFAULT_OPTION_COLOR)
        if isinstance(data, dict):
            return cls(
                name=str(data.get("name", "")).strip(),
                color=str(data.get("color") or DEFAULT_OPTION_COLOR).strip(),
            )
        return cls(name="", color=DEFAULT_OPTION_COLOR)


@dataclass
class ShotProperty:
    """Schema definition for a single Shot property (built-in or custom)."""

    key: str
    name: str
    prop_type: str = PropertyType.TEXT
    options: List[PropertyOption] = field(default_factory=list)
    allow_multiple: bool = False
    default_value: str = ""
    builtin: bool = False
    show_in_table: bool = True

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        self.key = str(self.key or "").strip() or slugify_property_key(self.name)
        self.prop_type = PropertyType.normalize(self.prop_type)
        self.options = [
            o if isinstance(o, PropertyOption) else PropertyOption.from_dict(o)
            for o in (self.options or [])
            if (o.name if isinstance(o, PropertyOption) else str(o.get("name", "") if isinstance(o, dict) else o).strip())
        ]
        self.allow_multiple = bool(self.allow_multiple)
        self.default_value = str(self.default_value or "")
        self.builtin = bool(self.builtin)
        self.show_in_table = bool(self.show_in_table)

    @property
    def storage_key(self) -> str:
        """
        Key used when reading/writing Shot values:
          - Built-in properties map directly to Shot attributes ('status', 'assign').
          - Custom properties map to their display name in Shot.custom_fields for
            backwards compatibility with Excel, Google Sheets, and existing project.json.
        """
        return self.key if self.builtin else self.name

    def option_names(self) -> List[str]:
        return [o.name for o in self.options if o.name]

    def get_option(self, name: str) -> Optional[PropertyOption]:
        target = (name or "").strip().lower()
        if not target:
            return None
        for opt in self.options:
            if opt.name.lower() == target:
                return opt
        return None

    def get_option_color(self, name: str, fallback: str = DEFAULT_OPTION_COLOR) -> str:
        opt = self.get_option(name)
        return opt.color if (opt and opt.color) else fallback

    def color_map(self) -> Dict[str, str]:
        return {o.name: o.color for o in self.options if o.name}

    @staticmethod
    def parse_multi_value(raw: str) -> List[str]:
        if not raw:
            return []
        parts = [p.strip() for p in str(raw).split(",") if p.strip()]
        seen = set()
        result: List[str] = []
        for p in parts:
            low = p.lower()
            if low not in seen:
                seen.add(low)
                result.append(p)
        return result

    @staticmethod
    def format_multi_value(items: List[str]) -> str:
        cleaned = [str(x).strip() for x in (items or []) if str(x).strip()]
        return ", ".join(cleaned)

    @staticmethod
    def parse_bool(raw: Any) -> bool:
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in ("true", "1", "yes", "y", "on", "checked")

    @staticmethod
    def format_bool(val: bool) -> str:
        return "true" if val else "false"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "prop_type": self.prop_type,
            "options": [o.to_dict() for o in self.options],
            "allow_multiple": self.allow_multiple,
            "default_value": self.default_value,
            "builtin": self.builtin,
            "show_in_table": self.show_in_table,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ShotProperty":
        raw_opts = data.get("options", [])
        options = [PropertyOption.from_dict(o) for o in raw_opts] if isinstance(raw_opts, list) else []
        name = str(data.get("name", "")).strip()
        key = str(data.get("key", "")).strip() or slugify_property_key(name)
        return cls(
            key=key,
            name=name or key,
            prop_type=str(data.get("prop_type", PropertyType.TEXT)),
            options=options,
            allow_multiple=bool(data.get("allow_multiple", False)),
            default_value=str(data.get("default_value", "")),
            builtin=bool(data.get("builtin", False)),
            show_in_table=bool(data.get("show_in_table", True)),
        )


def default_status_property() -> ShotProperty:
    """Factory for the built-in Status property with Notion-Dark default colors."""
    return ShotProperty(
        key="status",
        name="Status",
        prop_type=PropertyType.STATUS,
        options=[
            PropertyOption("Pending", "#5A5A58"),
            PropertyOption("WIP", "#C26D2D"),
            PropertyOption("Review", "#2383E2"),
            PropertyOption("Approved", "#2E7D5B"),
            PropertyOption("Final", "#3A8E82"),
            PropertyOption("Omit", "#C24E2D"),
        ],
        default_value="Pending",
        builtin=True,
        show_in_table=True,
    )


def default_artist_property() -> ShotProperty:
    """Factory for the built-in Artist (people) property."""
    return ShotProperty(
        key="assign",
        name="Artist",
        prop_type=PropertyType.PEOPLE,
        options=[],
        allow_multiple=False,
        default_value="",
        builtin=True,
        show_in_table=True,
    )
