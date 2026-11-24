import shlex
from typing import Dict, List, Optional

from config import load_user_settings, save_user_settings
from dccs.base_dcc import BaseDCCPlugin
from dccs.nuke_plugin import NukeDCCPlugin
from dccs.houdini_plugin import HoudiniDCCPlugin
from dccs.blender_plugin import BlenderDCCPlugin
from dccs.resolve_plugin import ResolveFusionDCCPlugin


# Master catalog of available DCC plugin classes.
# New DCC modules (e.g. Maya, Fusion, Unreal) can be registered here without modifying core UI code.
AVAILABLE_DCC_CLASSES = [
    NukeDCCPlugin,
    HoudiniDCCPlugin,
    BlenderDCCPlugin,
    ResolveFusionDCCPlugin,
]


class DCCRegistry:
    """
    Modular registry for DCC plugins.
    Allows users to enable/disable individual DCC modules and customize executable paths.
    """

    def __init__(self):
        self._plugins: Dict[str, BaseDCCPlugin] = {}
        self._enabled_ids: List[str] = []
        self.reload_from_settings()

    def reload_from_settings(self) -> None:
        settings = load_user_settings()
        self._enabled_ids = list(settings.get("enabled_dccs", ["nuke", "houdini", "blender"]))
        custom_paths = settings.get("dcc_paths", {})
        custom_envs = settings.get("dcc_env", {})
        custom_args = settings.get("dcc_args", {})

        self._plugins.clear()
        for cls in AVAILABLE_DCC_CLASSES:
            custom_exe = custom_paths.get(cls.dcc_id, "")
            raw_env = custom_envs.get(cls.dcc_id, {})
            extra_env = raw_env if isinstance(raw_env, dict) else {}
            raw_args = custom_args.get(cls.dcc_id, "")
            if isinstance(raw_args, list):
                extra_args = [str(a) for a in raw_args if str(a).strip()]
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    extra_args = shlex.split(raw_args)
                except ValueError:
                    extra_args = raw_args.split()
            else:
                extra_args = []
            plugin = cls(custom_exe_path=custom_exe)
            plugin._extra_env = dict(extra_env)
            plugin._extra_args = list(extra_args)
            self._plugins[plugin.dcc_id] = plugin

    def all_plugins(self) -> List[BaseDCCPlugin]:
        return [self._plugins[cls.dcc_id] for cls in AVAILABLE_DCC_CLASSES if cls.dcc_id in self._plugins]

    def enabled_plugins(self) -> List[BaseDCCPlugin]:
        return [p for p in self.all_plugins() if p.dcc_id in self._enabled_ids]

    def is_enabled(self, dcc_id: str) -> bool:
        return dcc_id in self._enabled_ids

    def set_enabled(self, dcc_id: str, enabled: bool) -> None:
        if enabled and dcc_id not in self._enabled_ids:
            self._enabled_ids.append(dcc_id)
        elif not enabled and dcc_id in self._enabled_ids:
            self._enabled_ids.remove(dcc_id)
        save_user_settings({"enabled_dccs": self._enabled_ids})

    def get_plugin(self, dcc_id: str) -> Optional[BaseDCCPlugin]:
        return self._plugins.get(dcc_id.lower())

    def get_plugin_for_extension(self, ext: str) -> Optional[BaseDCCPlugin]:
        if not ext.startswith("."):
            ext = f".{ext}"
        ext_low = ext.lower()
        for p in self.all_plugins():
            if ext_low in p.extensions:
                return p
        return None
