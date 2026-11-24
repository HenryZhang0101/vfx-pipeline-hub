import os
import glob
import sys
from typing import Dict, List, Tuple, Any

from dccs.base_dcc import BaseDCCPlugin


class ResolveFusionDCCPlugin(BaseDCCPlugin):
    r"""
    Modular DaVinci Resolve / Fusion Studio DCC Plugin (.drt timelines & .comp compositions).
    Tracks Resolve Timeline (.drt) and Fusion (.comp) scene versions inside scenes/Compositing/.
    """

    dcc_id = "resolve"
    display_name = "DaVinci Resolve / Fusion"
    badge_color = "#ff5252"
    extensions = (".drt", ".comp")
    default_department = "cmp"
    default_task = "comp"

    def auto_detect_executables(self) -> List[str]:
        found: List[str] = []
        candidates: List[str] = []
        if sys.platform == "win32":
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            candidates.append(
                os.path.join(program_files, "Blackmagic Design", "DaVinci Resolve", "Resolve.exe")
            )
            fusion_dirs = sorted(glob.glob(os.path.join(program_files, "Blackmagic Design", "Fusion*")), reverse=True)
            for fd in fusion_dirs:
                candidates.append(os.path.join(fd, "Fusion.exe"))
        elif sys.platform == "darwin":
            candidates.append("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/Resolve")
            for fd in sorted(glob.glob("/Applications/Blackmagic Fusion*"), reverse=True):
                candidates.append(os.path.join(fd, "Fusion.app", "Contents", "MacOS", "Fusion"))

        for c in candidates:
            if os.path.isfile(c):
                found.append(os.path.normpath(c))
        return found

    def create_scene(
        self,
        scene_filepath: str,
        shot_context: Dict[str, Any],
        bootstrap: bool = True,
    ) -> Tuple[bool, str]:
        """Creates a Fusion .comp script or .drt placeholder in Scenefiles."""
        os.makedirs(os.path.dirname(scene_filepath), exist_ok=True)
        if scene_filepath.lower().endswith(".comp"):
            start_f = int(shot_context.get("start_frame", 1001))
            end_f = int(shot_context.get("end_frame", 1065))
            comp_str = (
                "Composition {\n"
                f"\tCurrentTime = {start_f},\n"
                f"\tRenderRange = {{ {start_f}, {end_f} }},\n"
                f"\tGlobalRange = {{ {start_f}, {end_f} }},\n"
                "\tTools = {\n\t}\n"
                "}\n"
            )
            with open(scene_filepath, "w", encoding="utf-8") as f:
                f.write(comp_str)
            return True, f"Created Fusion composition {os.path.basename(scene_filepath)}"

        with open(scene_filepath, "wb") as f:
            f.write(b"")
        return True, f"Created Resolve scene {os.path.basename(scene_filepath)}"
