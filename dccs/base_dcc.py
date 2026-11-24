import os
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple, Any


class BaseDCCPlugin:
    r"""
    Abstract base class for modular DCC integrations (Nuke, Houdini, Blender, Maya, Fusion, etc.).
    Each DCC lives in its own module under `h:\_pipeline\dccs\`.
    """

    dcc_id: str = "base"
    display_name: str = "Base DCC"
    badge_color: str = "#61afef"
    extensions: Tuple[str, ...] = (".scene",)
    default_department: str = "cmp"
    default_task: str = "comp"

    def __init__(
        self,
        custom_exe_path: str = "",
        extra_env: Optional[Dict[str, str]] = None,
        extra_args: Optional[List[str]] = None,
    ):
        self._custom_exe_path = custom_exe_path.strip() if custom_exe_path else ""
        self._extra_env: Dict[str, str] = dict(extra_env or {})
        self._extra_args: List[str] = list(extra_args or [])

    @property
    def primary_extension(self) -> str:
        return self.extensions[0] if self.extensions else ".scene"

    def auto_detect_executables(self) -> List[str]:
        """Returns a list of discovered executable paths on the workstation (newest first)."""
        return []

    def get_executable(self) -> str:
        """Returns user-configured executable path if valid, otherwise best auto-detected path."""
        if self._custom_exe_path and os.path.isfile(self._custom_exe_path):
            return os.path.normpath(self._custom_exe_path)
        found = self.auto_detect_executables()
        return os.path.normpath(found[0]) if found else ""

    def set_executable(self, path: str) -> None:
        self._custom_exe_path = path.strip() if path else ""

    def is_installed(self) -> bool:
        exe = self.get_executable()
        return bool(exe and os.path.isfile(exe))

    def build_environment(self, shot_context: Dict[str, Any]) -> Dict[str, str]:
        """Builds OS environment variables passed to the DCC process on launch."""
        from config import PIPELINE_ROOT, get_ocio_config
        from core.timecode import format_shot_id

        env = os.environ.copy()
        vfx_root = str(shot_context.get("vfx_root") or shot_context.get("prism_root") or "")
        seq = str(shot_context.get("seq", ""))
        shot = str(shot_context.get("shot", ""))
        shot_id = str(shot_context.get("shot_id") or (format_shot_id(seq, shot) if (seq and shot) else ""))

        env["VFX_PIPELINE_ROOT"] = PIPELINE_ROOT
        env["VFX_PROJECT_NAME"] = str(shot_context.get("project_name", ""))
        env["VFX_PROJECT_ROOT"] = vfx_root
        env["VFX_VFX_ROOT"] = vfx_root
        env["VFX_PRISM_ROOT"] = vfx_root  # legacy alias
        env["VFX_SEQ"] = seq
        env["VFX_SHOT"] = shot
        env["VFX_SHOT_ID"] = shot_id
        env["VFX_START_FRAME"] = str(shot_context.get("start_frame", 1001))
        env["VFX_END_FRAME"] = str(shot_context.get("end_frame", 1065))
        env["VFX_FPS"] = str(shot_context.get("fps", 24.0))
        if shot_context.get("department"):
            env["VFX_DEPARTMENT"] = str(shot_context["department"])
        if shot_context.get("version"):
            env["VFX_SCENE_VERSION"] = str(shot_context["version"])
        ocio_cfg = get_ocio_config(str(shot_context.get("ocio_config") or ""))
        if ocio_cfg and os.path.isfile(ocio_cfg):
            env["OCIO"] = os.path.normpath(ocio_cfg)
        for k, v in self._extra_env.items():
            if k:
                env[str(k)] = str(v)
        return env

    def create_scene(
        self,
        scene_filepath: str,
        shot_context: Dict[str, Any],
        bootstrap: bool = True,
    ) -> Tuple[bool, str]:
        """
        Creates a new scene file on disk for the given shot context.
        Subclasses override this to generate smart bootstrapped files (e.g. Nuke .nk script).
        """
        os.makedirs(os.path.dirname(scene_filepath), exist_ok=True)
        with open(scene_filepath, "w", encoding="utf-8") as f:
            f.write("")
        return True, f"Created {os.path.basename(scene_filepath)}"

    def version_up_scene(
        self,
        source_filepath: str,
        new_filepath: str,
        shot_context: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Copies an existing scene file to a new version (v####) and updates internal version paths if applicable.
        """
        if not os.path.isfile(source_filepath):
            return False, f"Source scene does not exist: {source_filepath}"
        try:
            os.makedirs(os.path.dirname(new_filepath), exist_ok=True)
            shutil.copy2(source_filepath, new_filepath)
            return True, f"Versioned up to {os.path.basename(new_filepath)}"
        except Exception as e:
            return False, f"Failed to version up scene: {e}"

    def launch_scene(
        self,
        scene_filepath: str,
        shot_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Launches the DCC executable with the given scene file."""
        exe = self.get_executable()
        if not exe or not os.path.isfile(exe):
            return False, f"{self.display_name} executable not found. Configure path in Module Settings."

        if not os.path.isfile(scene_filepath):
            return False, f"Scene file does not exist: {scene_filepath}"

        ctx = shot_context or {}
        env = self.build_environment(ctx)
        cmd = list(self.build_launch_command(exe, scene_filepath, ctx))
        if self._extra_args:
            cmd = [cmd[0], *self._extra_args, *cmd[1:]]
        try:
            subprocess.Popen(cmd, cwd=os.path.dirname(scene_filepath), env=env)
            return True, f"Launched {self.display_name}: {os.path.basename(scene_filepath)}"
        except Exception as e:
            return False, f"Failed to launch {self.display_name}: {e}"

    def build_launch_command(
        self,
        exe: str,
        scene_filepath: str,
        shot_context: Dict[str, Any],
    ) -> List[str]:
        return [exe, os.path.normpath(scene_filepath)]
