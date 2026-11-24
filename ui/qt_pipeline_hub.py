"""
Compatibility shim for DaVinci Resolve VFX Pipeline Hub.
Delegates to the refactored Notion-Dark implementation in `ui.resolve_hub_window`
and `ui.dialogs.resolve_dialogs`.
"""

from ui.dialogs.resolve_dialogs import (
    AssignShotDialog,
    BatchAssignTimelineDialog,
    ResolveExportDialog,
    ResolveLoadCompsDialog,
)
from ui.resolve_hub_window import ResolveHubWindow, main

VFXPipelineHub = ResolveHubWindow
ExportPlatesDialog = ResolveExportDialog
LoadCompsDialog = ResolveLoadCompsDialog

__all__ = [
    "AssignShotDialog",
    "BatchAssignTimelineDialog",
    "ExportPlatesDialog",
    "LoadCompsDialog",
    "ResolveExportDialog",
    "ResolveHubWindow",
    "ResolveLoadCompsDialog",
    "VFXPipelineHub",
    "main",
]

if __name__ == "__main__":
    main()
