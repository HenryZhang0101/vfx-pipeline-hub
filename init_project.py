r"""
Headless / CLI initializer for a VFX Project Workspace.
Usage:
    python init_project.py "h:\261105_pipedev"
"""
import os
import sys

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import config  # noqa: F401
from core.io.excel import load_shots_from_csv, save_shots_to_excel
from core.models import Project
from core.storage import ProjectRepository


def initialize_project_workspace(root_dir: str, project_name: str = "", fps: float = 24.0, resolution=(1920, 1080)):
    repo = ProjectRepository()
    project = Project(root_dir=root_dir, name=project_name, fps=fps, resolution=resolution)
    ok, msg = repo.initialize_structure(project)
    print(f"[Workspace] {msg}")

    if not os.path.exists(project.default_excel_path):
        initial_shots = load_shots_from_csv(project.legacy_csv_path) if os.path.exists(project.legacy_csv_path) else []
        project.replace_shots(initial_shots)
        save_shots_to_excel(
            project.default_excel_path,
            initial_shots,
            thumb_dir=project.thumbnails_dir,
            project_name=project.name,
        )
        print(f"[Excel] Created linked VFX Excel: {project.default_excel_path}")

    repo.save(project)
    return ok, project, repo


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else r"h:\261105_pipedev"
    initialize_project_workspace(target)
