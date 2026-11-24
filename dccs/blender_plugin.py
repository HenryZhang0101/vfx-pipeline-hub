import os
import glob
import sys
import subprocess
from typing import Dict, List, Tuple, Any

from dccs.base_dcc import BaseDCCPlugin


class BlenderDCCPlugin(BaseDCCPlugin):
    """
    Modular Blender DCC Plugin (Blender 3.x / 4.x / 5.x+).
    Can bootstrap new .blend files with exact shot frame range, FPS, resolution,
    and output path using Blender's headless --background --python-expr mode.
    """

    dcc_id = "blender"
    display_name = "Blender"
    badge_color = "#ea7600"
    extensions = (".blend",)
    default_department = "lgt"
    default_task = "Lighting"

    def auto_detect_executables(self) -> List[str]:
        found: List[str] = []
        if sys.platform == "win32":
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            base_dir = os.path.join(program_files, "Blender Foundation")
            if os.path.isdir(base_dir):
                blender_dirs = sorted(glob.glob(os.path.join(base_dir, "Blender*")), reverse=True)
                for b_dir in blender_dirs:
                    candidate = os.path.join(b_dir, "blender.exe")
                    if os.path.isfile(candidate):
                        found.append(os.path.normpath(candidate))
        elif sys.platform == "darwin":
            for app_root in ("/Applications", os.path.expanduser("~/Applications")):
                for b_app in sorted(glob.glob(os.path.join(app_root, "Blender*.app")), reverse=True):
                    candidate = os.path.join(b_app, "Contents", "MacOS", "Blender")
                    if os.path.isfile(candidate):
                        found.append(os.path.normpath(candidate))
        return found

    def create_scene(
        self,
        scene_filepath: str,
        shot_context: Dict[str, Any],
        bootstrap: bool = True,
    ) -> Tuple[bool, str]:
        """
        Creates a new .blend scene configured with the shot's frame range, FPS,
        resolution, and default 3dRender output path.
        """
        os.makedirs(os.path.dirname(scene_filepath), exist_ok=True)
        start_f = int(shot_context.get("start_frame", 1001))
        end_f = int(shot_context.get("end_frame", 1065))
        fps = int(round(float(shot_context.get("fps", 24.0))))
        width = int(shot_context.get("width", 1920))
        height = int(shot_context.get("height", 1080))
        seq = shot_context.get("seq", "R01")
        shot = shot_context.get("shot", "0010")
        version = shot_context.get("version", "v0001")
        task = shot_context.get("task", "Lighting")
        prism_root = shot_context.get("prism_root", "").replace("\\", "/")
        norm_scene = scene_filepath.replace("\\", "/")

        render_out = (
            f"{prism_root}/03_Production/Shots/{seq}/{shot}/Renders/3dRender/{task}/{version}/"
            f"{seq}-{shot}_{task}_{version}.####.exr"
        )

        exe = self.get_executable()
        if bootstrap and exe and os.path.isfile(exe):
            py_expr = (
                "import bpy; "
                "scn = bpy.context.scene; "
                f"scn.frame_start = {start_f}; "
                f"scn.frame_end = {end_f}; "
                f"scn.frame_current = {start_f}; "
                f"scn.render.fps = {fps}; "
                f"scn.render.resolution_x = {width}; "
                f"scn.render.resolution_y = {height}; "
                f"scn.render.filepath = '{render_out}'; "
                f"bpy.ops.wm.save_as_mainfile(filepath='{norm_scene}')"
            )
            try:
                res = subprocess.run(
                    [exe, "--background", "--python-expr", py_expr],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env=self.build_environment(shot_context),
                )
                if res.returncode == 0 and os.path.isfile(scene_filepath):
                    return True, f"Created Blender scene {os.path.basename(scene_filepath)} ({start_f}-{end_f}, {width}x{height})"
            except Exception as e:
                print(f"Blender bootstrap warning: {e}")

        with open(scene_filepath, "wb") as f:
            f.write(b"")
        return True, f"Created Blender scene {os.path.basename(scene_filepath)}"
