"""Foundry Nuke init.py — loaded automatically by Nuke when dccs/nuke is on NUKE_PATH."""

import os
import sys

# Tell config.py we are inside Nuke so h:\_pipeline\lib (PySide6) is NOT prepended to sys.path
os.environ.setdefault("VFX_PIPELINE_HOST_DCC", "nuke")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PIPELINE_ROOT not in sys.path:
    sys.path.append(_PIPELINE_ROOT)

# Configure pipeline OCIO config for both DCC Launcher and standalone Nuke launches
try:
    from config import get_ocio_config

    _had_env_ocio = bool(os.environ.get("OCIO") and os.path.isfile(os.environ.get("OCIO", "")))
    _ocio_cfg = os.environ["OCIO"] if _had_env_ocio else get_ocio_config()
    if _ocio_cfg and os.path.isfile(_ocio_cfg):
        _ocio_norm = os.path.normpath(_ocio_cfg)
        if not _had_env_ocio:
            os.environ["OCIO"] = _ocio_norm
        try:
            import nuke  # type: ignore

            nuke.knobDefault("Root.colorManagement", "OCIO")
            nuke.knobDefault("Root.OCIO_config", "custom")
            nuke.knobDefault("Root.workingSpaceLUT", "scene_linear")
            if not _had_env_ocio:
                nuke.knobDefault("Root.customOCIOConfigPath", _ocio_norm.replace("\\", "/"))
        except Exception:
            pass
except Exception as _ocio_exc:
    print(f"[VFX Pipeline] Warning configuring OCIO in Nuke init.py: {_ocio_exc}")

try:
    from dccs.nuke.callbacks import register_headless_callbacks

    register_headless_callbacks()
except Exception as _exc:
    try:
        import nuke  # type: ignore

        nuke.tprint(f"[VFX Pipeline] Warning during Nuke init.py: {_exc}")
    except Exception:
        print(f"[VFX Pipeline] Warning during Nuke init.py: {_exc}")
