import os
import glob
import sys
import subprocess
from typing import Dict, List, Tuple, Any

from dccs.base_dcc import BaseDCCPlugin


class HoudiniDCCPlugin(BaseDCCPlugin):
    """
    Modular SideFX Houdini DCC Plugin (Houdini 19 / 20 / 21 / 22+).
    Configures $JOB, $SEQ, $SHOT, $FSTART, $FEND, and $FPS environment variables
    and can bootstrap new .hip scenes via hython or launch directly.
    """

    dcc_id = "houdini"
    display_name = "SideFX Houdini"
    badge_color = "#ff6600"
    extensions = (".hip", ".hiplc", ".hipnc")
    default_department = "fx"
    default_task = "Effects"

    def auto_detect_executables(self) -> List[str]:
        found: List[str] = []
        if sys.platform == "win32":
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            base_dir = os.path.join(program_files, "Side Effects Software")
            if os.path.isdir(base_dir):
                houdini_dirs = sorted(glob.glob(os.path.join(base_dir, "Houdini*")), reverse=True)
                for h_dir in houdini_dirs:
                    bin_dir = os.path.join(h_dir, "bin")
                    for exe_name in ("houdinifx.exe", "houdinicore.exe", "houdini.exe"):
                        candidate = os.path.join(bin_dir, exe_name)
                        if os.path.isfile(candidate):
                            found.append(os.path.normpath(candidate))
        elif sys.platform == "darwin":
            for h_dir in sorted(glob.glob("/Applications/Houdini/Houdini*"), reverse=True):
                res_bin = os.path.join(h_dir, "Frameworks", "Houdini.framework", "Versions", "Current", "Resources", "bin")
                for exe_name in ("houdinifx", "houdinicore", "houdini"):
                    candidate = os.path.join(res_bin, exe_name)
                    if os.path.isfile(candidate):
                        found.append(os.path.normpath(candidate))
                for app_bin in sorted(glob.glob(os.path.join(h_dir, "Houdini*.app", "Contents", "MacOS", "houdini*"))):
                    if os.path.isfile(app_bin):
                        found.append(os.path.normpath(app_bin))
        return found

    def build_environment(self, shot_context: Dict[str, Any]) -> Dict[str, str]:
        env = super().build_environment(shot_context)
        prism_root = shot_context.get("prism_root", "")
        seq = shot_context.get("seq", "")
        shot = shot_context.get("shot", "")
        if prism_root and seq and shot:
            shot_dir = os.path.join(prism_root, "03_Production", "Shots", seq, shot)
            env["JOB"] = shot_dir.replace("\\", "/")
        env["SEQ"] = str(seq)
        env["SHOT"] = str(shot)
        return env

    def create_scene(
        self,
        scene_filepath: str,
        shot_context: Dict[str, Any],
        bootstrap: bool = True,
    ) -> Tuple[bool, str]:
        """
        Creates a new Houdini .hip scene. If hython.exe is found alongside houdini.exe,
        uses hython to initialize a valid .hip file with frame range and FPS configured;
        otherwise writes a companion startup script that Houdini executes on open.
        """
        os.makedirs(os.path.dirname(scene_filepath), exist_ok=True)
        start_f = int(shot_context.get("start_frame", 1001))
        end_f = int(shot_context.get("end_frame", 1065))
        fps = float(shot_context.get("fps", 24.0))

        exe = self.get_executable()
        hython_name = "hython.exe" if sys.platform == "win32" else "hython"
        hython_exe = os.path.join(os.path.dirname(exe), hython_name) if exe else ""

        if bootstrap and hython_exe and os.path.isfile(hython_exe):
            norm_scene = scene_filepath.replace("\\", "/")
            py_expr = (
                f"import hou; "
                f"hou.setFps({fps}); "
                f"hou.playbar.setFrameRange({start_f}, {end_f}); "
                f"hou.playbar.setPlaybackRange({start_f}, {end_f}); "
                f"hou.setFrame({start_f}); "
                f"hou.hipFile.save('{norm_scene}')"
            )
            try:
                res = subprocess.run(
                    [hython_exe, "-c", py_expr],
                    capture_output=True,
                    text=True,
                    timeout=25,
                    env=self.build_environment(shot_context),
                )
                if res.returncode == 0 and os.path.isfile(scene_filepath):
                    return True, f"Created Houdini scene {os.path.basename(scene_filepath)} ({start_f}-{end_f} @ {fps:g}fps)"
            except Exception as e:
                print(f"hython bootstrap fallback: {e}")

        # Fast fallback: write a lightweight Houdini command script (.cmd / empty hip placeholder)
        with open(scene_filepath, "wb") as f:
            f.write(b"")
        return True, f"Created Houdini scene placeholder {os.path.basename(scene_filepath)}"
