import os
from typing import List


class BaseToolPlugin:
    r"""
    Abstract base class for modular pipeline tools under `h:\_pipeline\tools\`.
    Examples: DJV Media Player & Compare, Media/Plate Ingester, Slate Generator,
    FFmpeg Converter, Daily Playlist Builder, OCIO LUT Manager, etc.
    """

    tool_id: str = "base_tool"
    display_name: str = "Base Tool"
    category: str = "General"
    description: str = ""
    requires_executable: bool = False

    def __init__(self, custom_exe_path: str = ""):
        self._custom_exe_path = custom_exe_path.strip() if custom_exe_path else ""

    def auto_detect_executables(self) -> List[str]:
        return []

    def get_executable(self) -> str:
        if self._custom_exe_path and os.path.isfile(self._custom_exe_path):
            return os.path.normpath(self._custom_exe_path)
        found = self.auto_detect_executables()
        return os.path.normpath(found[0]) if found else ""

    def set_executable(self, path: str) -> None:
        self._custom_exe_path = path.strip() if path else ""

    def is_available(self) -> bool:
        if not self.requires_executable:
            return True
        exe = self.get_executable()
        return bool(exe and os.path.isfile(exe))
