"""
DaVinci Resolve Utility Launcher: VFX Pipeline Hub
Installed by setup_client.py into DaVinci Resolve's Fusion/Scripts/Utility folder.
"""
import glob
import importlib
import os
import sys

PIPELINE_ROOT = r"__PIPELINE_ROOT__"
if not os.path.isdir(PIPELINE_ROOT):
    PIPELINE_ROOT = os.environ.get("VFX_PIPELINE_ROOT", "").strip()

if not PIPELINE_ROOT or not os.path.isdir(PIPELINE_ROOT):
    raise RuntimeError(
        "VFX Pipeline Hub installation folder not found. "
        "Please re-run Setup_Windows.bat / Setup_macOS.command from your VFX Pipeline Hub folder."
    )

# Add bundled dependencies (Windows: lib/, macOS: .venv_mac site-packages & lib_mac/)
_lib_candidates = []
if sys.platform == "win32":
    _lib_candidates.append(os.path.join(PIPELINE_ROOT, "lib"))
elif sys.platform == "darwin":
    _lib_candidates.append(os.path.join(PIPELINE_ROOT, "lib_mac"))
    _lib_candidates.extend(
        sorted(glob.glob(os.path.join(PIPELINE_ROOT, ".venv_mac", "lib", "python*", "site-packages")), reverse=True)
    )
else:
    _lib_candidates.append(os.path.join(PIPELINE_ROOT, "lib_linux"))

for _lib_dir in _lib_candidates:
    if _lib_dir and os.path.isdir(_lib_dir) and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)

if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

# Reload pipeline modules when re-running inside an existing Resolve session
_RELOAD_PREFIXES = ("core.", "ui.", "tools.", "dccs.")
for mod_name in sorted(list(sys.modules.keys())):
    if mod_name == "config" or mod_name.startswith(_RELOAD_PREFIXES):
        try:
            importlib.reload(sys.modules[mod_name])
        except Exception:
            pass

from ui.resolve_hub_window import ResolveHubWindow, main

VFXPipelineHub = ResolveHubWindow

if __name__ == "__main__":
    main()
