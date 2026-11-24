r"""
Standalone VFX DCC Launcher Entry Point.
Usage:
    python h:\_pipeline\dcc_launcher.py [optional_project_root]
"""
import argparse
import os
import sys

PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

import config  # noqa: F401 — ensures PIPELINE_LIB is added to sys.path
from qtpy.QtWidgets import QApplication
from ui.dcc_launcher_window import DCCLauncherWindow
from ui.theme import get_app_icon


def launch_dcc_launcher(project_root: str = "", initial_shot_id: str = "") -> DCCLauncherWindow:
    config.set_windows_app_id()
    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv or ["vfx_dcc_launcher"])
        owns_app = True

    icon = get_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    win = DCCLauncherWindow(initial_project_root=project_root, initial_shot_id=initial_shot_id)
    win.show()
    win.raise_()
    win.activateWindow()

    if owns_app:
        sys.exit(app.exec_())
    return win


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VFX Pipeline DCC Launcher")
    parser.add_argument("project_root", nargs="?", default="", help="Optional project root directory")
    parser.add_argument("--shot", default="", help="Optional initial Shot ID to pre-select")
    args = parser.parse_args()
    launch_dcc_launcher(project_root=args.project_root, initial_shot_id=args.shot)
