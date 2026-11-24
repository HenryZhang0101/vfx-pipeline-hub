"""
Legacy entry point forwarder:
Redirects to the refactored Notion-Dark DaVinci Resolve Hub in `ui.resolve_hub_window`.
"""

import os
import sys

from config import PIPELINE_LIB

if PIPELINE_LIB not in sys.path and os.path.isdir(PIPELINE_LIB):
    sys.path.insert(0, PIPELINE_LIB)

from ui.qt_pipeline_hub import (
    AssignShotDialog,
    BatchAssignTimelineDialog,
    ExportPlatesDialog,
    LoadCompsDialog,
    ResolveHubWindow,
    VFXPipelineHub,
    main,
)

__all__ = [
    "AssignShotDialog",
    "BatchAssignTimelineDialog",
    "ExportPlatesDialog",
    "LoadCompsDialog",
    "ResolveHubWindow",
    "VFXPipelineHub",
    "main",
]

if __name__ == "__main__":
    main()
