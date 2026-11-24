r"""
Compatibility Dispatcher for VFX Pipeline Workstation Hub.
Routes to Project Manager (default) or DCC Launcher (--mode dcc).
"""
import argparse
import os
import sys

PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

import config  # noqa: F401
from dcc_launcher import launch_dcc_launcher
from project_manager import launch_project_manager


def launch_standalone(project_root: str = "", mode: str = "media"):
    if mode == "dcc":
        return launch_dcc_launcher(project_root=project_root)
    return launch_project_manager(project_root=project_root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VFX Pipeline Workstation Hub")
    parser.add_argument("project_root", nargs="?", default="", help="Optional project root directory")
    parser.add_argument(
        "--mode",
        choices=["media", "manager", "dcc"],
        default="manager",
        help="App to launch: manager (Project Manager) or dcc (DCC Launcher)",
    )
    args = parser.parse_args()
    launch_standalone(project_root=args.project_root, mode=args.mode)
