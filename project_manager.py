r"""
Standalone VFX Project Manager Entry Point.
Usage:
    python h:\_pipeline\project_manager.py [optional_project_root]
"""
import argparse
import os
import sys

PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

import config  # noqa: F401 — ensures PIPELINE_LIB is added to sys.path
from qtpy.QtWidgets import QApplication
from ui.project_manager_window import ProjectManagerWindow
from ui.theme import get_app_icon


def launch_project_manager(project_root: str = "") -> ProjectManagerWindow:
    config.set_windows_app_id()
    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv or ["vfx_project_manager"])
        owns_app = True

    icon = get_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    win = ProjectManagerWindow(initial_project_root=project_root)
    win.show()
    win.raise_()
    win.activateWindow()

    if owns_app:
        sys.exit(app.exec_())
    return win


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VFX Pipeline Project Manager")
    parser.add_argument("project_root", nargs="?", default="", help="Optional project root directory")
    args = parser.parse_args()
    launch_project_manager(project_root=args.project_root)
