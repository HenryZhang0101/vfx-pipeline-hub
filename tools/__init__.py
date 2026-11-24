from typing import Dict, List, Optional

from config import DEFAULT_ENABLED_TOOLS, load_user_settings, save_user_settings
from tools.base_tool import BaseToolPlugin
from tools.djv_tool import DJVPlayerTool
from tools.media_ingester import MediaIngesterTool
from tools.slate_delivery_tool import SlateDeliveryTool


# Master catalog of available Pipeline Tool classes.
# Future tools (FFmpeg Converter, Daily Playlist, OCIO LUT Tool, etc.)
# register here as self-contained modules.
AVAILABLE_TOOL_CLASSES = [
    DJVPlayerTool,
    MediaIngesterTool,
    SlateDeliveryTool,
]


class ToolRegistry:
    r"""
    Modular registry for pipeline tools under `h:\_pipeline\tools\`.
    Allows enabling/disabling tools per user and configuring custom binary paths.
    """

    def __init__(self):
        self._tools: Dict[str, BaseToolPlugin] = {}
        self._enabled_ids: List[str] = []
        self.reload_from_settings()

    def reload_from_settings(self) -> None:
        settings = load_user_settings()
        saved_enabled = list(settings.get("enabled_tools", DEFAULT_ENABLED_TOOLS))
        disabled_tools = set(settings.get("disabled_tools", []))
        for tid in DEFAULT_ENABLED_TOOLS:
            if tid not in saved_enabled and tid not in disabled_tools:
                saved_enabled.append(tid)
        self._enabled_ids = saved_enabled
        custom_paths = settings.get("tool_paths", {})

        self._tools.clear()
        for cls in AVAILABLE_TOOL_CLASSES:
            custom_exe = custom_paths.get(cls.tool_id, "")
            tool = cls(custom_exe_path=custom_exe)
            self._tools[tool.tool_id] = tool

    def all_tools(self) -> List[BaseToolPlugin]:
        return [self._tools[cls.tool_id] for cls in AVAILABLE_TOOL_CLASSES if cls.tool_id in self._tools]

    def enabled_tools(self) -> List[BaseToolPlugin]:
        return [t for t in self.all_tools() if t.tool_id in self._enabled_ids]

    def is_enabled(self, tool_id: str) -> bool:
        return tool_id in self._enabled_ids

    def set_enabled(self, tool_id: str, enabled: bool) -> None:
        settings = load_user_settings()
        disabled = set(settings.get("disabled_tools", []))
        if enabled and tool_id not in self._enabled_ids:
            self._enabled_ids.append(tool_id)
            disabled.discard(tool_id)
        elif not enabled and tool_id in self._enabled_ids:
            self._enabled_ids.remove(tool_id)
            disabled.add(tool_id)
        save_user_settings({"enabled_tools": self._enabled_ids, "disabled_tools": list(disabled)})

    def get_tool(self, tool_id: str) -> Optional[BaseToolPlugin]:
        return self._tools.get(tool_id.lower())

    @property
    def djv(self) -> Optional[DJVPlayerTool]:
        t = self._tools.get("djv_player")
        return t if (t and self.is_enabled("djv_player")) else None

    @property
    def ingester(self) -> Optional[MediaIngesterTool]:
        t = self._tools.get("media_ingester")
        return t if (t and self.is_enabled("media_ingester")) else None

    @property
    def slate_delivery(self) -> Optional[SlateDeliveryTool]:
        t = self._tools.get("slate_delivery")
        return t if (t and self.is_enabled("slate_delivery")) else None
