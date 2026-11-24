"""Compatibility re-exports for Resolve Hub (qt_pipeline_hub.py)."""

from ui.dcc_launcher_window import DCCLauncherWindow
from ui.dialogs import (
    BatchQTExportDialog,
    CreateDCCSceneDialog,
    MediaIngestDialog,
    ModuleSettingsDialog,
    NewShotDialog,
    ProjectInitDialog,
    ShotPropertiesDialog,
)
from ui.project_manager_window import ProjectManagerWindow


def VFXStandaloneWindow(initial_project_root: str = "", initial_mode: str = "media"):
    if initial_mode == "dcc":
        return DCCLauncherWindow(initial_project_root=initial_project_root)
    return ProjectManagerWindow(initial_project_root=initial_project_root)


__all__ = [
    "BatchQTExportDialog",
    "CreateDCCSceneDialog",
    "DCCLauncherWindow",
    "MediaIngestDialog",
    "ModuleSettingsDialog",
    "NewShotDialog",
    "ProjectInitDialog",
    "ProjectManagerWindow",
    "ShotPropertiesDialog",
    "VFXStandaloneWindow",
]
