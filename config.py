import os
import shutil
import subprocess
import sys


def _resolve_media_bin(name: str) -> str:
    """Resolve a bundled binary in bin/ or fall back to PATH / Homebrew on macOS/Linux."""
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    bundled = os.path.join(PIPELINE_BIN_DIR, exe_name)
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "darwin":
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            cand = os.path.join(prefix, name)
            if os.path.isfile(cand):
                return cand
    return bundled


# --- Core Paths ---
PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
PIPELINE_LIB = os.path.join(PIPELINE_ROOT, "lib" if sys.platform == "win32" else "lib_mac")
PIPELINE_BIN_DIR = os.path.join(PIPELINE_ROOT, "bin")
PIPELINE_OCIO_DIR = os.path.join(PIPELINE_ROOT, "ocio")
DEFAULT_OCIO_CONFIG_NAME = "fn-nuke_studio-config-v2.2.0_aces-v1.3_ocio-v2.4.ocio"
DEFAULT_OCIO_CONFIG = os.path.join(PIPELINE_OCIO_DIR, DEFAULT_OCIO_CONFIG_NAME)
PIPELINE_ASSETS_DIR = os.path.join(PIPELINE_ROOT, "assets")
PIPELINE_ICONS_DIR = os.path.join(PIPELINE_ASSETS_DIR, "icons")
APP_VERSION = "1.0.1"
GITHUB_REPO = "HenryZhang0101/vfx-pipeline-hub"
APP_ICON_PNG = os.path.join(PIPELINE_ICONS_DIR, "pipeline_hub.png")
APP_ICON_ICO = os.path.join(PIPELINE_ICONS_DIR, "pipeline_hub.ico")
APP_ICON_ICNS = os.path.join(PIPELINE_ICONS_DIR, "pipeline_hub.icns")
WINDOWS_APP_ID = "VFXPipelineHub.Workstation"
DEFAULT_SLATE_DISCLAIMER = "FOR VFX REVIEW ONLY — NOT FOR DISTRIBUTION"
FFMPEG_BIN = _resolve_media_bin("ffmpeg")
FFPROBE_BIN = _resolve_media_bin("ffprobe")

# Avoid shadowing a host DCC's bundled PySide6/shiboken6 (e.g. Nuke 16+, Houdini, Resolve)
_IS_HOST_DCC = bool(os.environ.get("VFX_PIPELINE_HOST_DCC")) or ("nuke" in sys.modules)
if not _IS_HOST_DCC and PIPELINE_LIB not in sys.path and os.path.isdir(PIPELINE_LIB):
    sys.path.insert(0, PIPELINE_LIB)

if sys.platform == "win32":
    RESOLVE_UTILITY_DIR = r"c:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility"
elif sys.platform == "darwin":
    RESOLVE_UTILITY_DIR = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
else:
    RESOLVE_UTILITY_DIR = "/opt/resolve/Fusion/Scripts/Utility"


def open_path_in_os(path: str) -> None:
    """Open a file or folder in the host OS default handler (Windows, macOS, Linux)."""
    if not path:
        return
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def set_windows_app_id(app_id: str = WINDOWS_APP_ID) -> None:
    """Set explicit Windows AppUserModelID so taskbar displays our app icon instead of python.exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

# --- Standardized Project Folder Structure ---
DEFAULT_EXPORT_FOLDER = "_export"

PROJECT_FOLDERS = [
    "01_Client_Raw",
    "02_Resources",
    os.path.join("02_Resources", "Documents"),
    os.path.join("02_Resources", "Downloads"),
    os.path.join("02_Resources", "Fonts"),
    "03_Editing",
    os.path.join("03_Editing", "Resolve_Projects"),
    os.path.join("03_Editing", "Shot_Sessions"),
    os.path.join("03_Editing", "Timelines_EDL"),
    os.path.join("03_Editing", "Audio"),
    "04_VFX",
    "05_Exports",
    os.path.join("05_Exports", "WIP_Review"),
    os.path.join("05_Exports", "Final_Deliverables"),
]

FOLDER_CLIENT_RAW = "01_Client_Raw"
FOLDER_RESOURCES = "02_Resources"
FOLDER_EDITING = "03_Editing"
FOLDER_SHOT_SESSIONS = os.path.join("03_Editing", "Shot_Sessions")
FOLDER_VFX = "04_VFX"
FOLDER_EXPORTS = DEFAULT_EXPORT_FOLDER

# --- Shot & VFX Defaults ---
DEFAULT_STATUSES = ["Pending", "WIP", "Review", "Approved", "Final", "Omit"]
DEFAULT_SEQ_PREFIX = "R01"
DEFAULT_SHOT_START = 10
DEFAULT_SHOT_STEP = 10
DEFAULT_START_FRAME = 1001
DEFAULT_HANDLES = 10
DEFAULT_PLATE_IDENTIFIER = "plate"
DEFAULT_COMP_IDENTIFIER = "comp"
DEFAULT_VERSION = "v0001"
DEFAULT_RENDER_PRESET = "09_VFX"

# --- Common Frame Rates & Resolution Presets ---
COMMON_FPS_PRESETS = [
    ("23.976 fps (NTSC Film)", 23.976),
    ("24 fps (Cinema)", 24.0),
    ("25 fps (PAL)", 25.0),
    ("29.97 fps (NTSC)", 29.97),
    ("30 fps", 30.0),
    ("47.952 fps (HFR NTSC)", 47.952),
    ("48 fps (HFR Cinema)", 48.0),
    ("50 fps (PAL HFR)", 50.0),
    ("59.94 fps (NTSC HFR)", 59.94),
    ("60 fps", 60.0),
    ("120 fps", 120.0),
]

DEFAULT_RESOLUTION_PRESETS = [
    ("HD 1080p", 1920.0, 1080.0),
    ("2K DCI", 2048.0, 1080.0),
    ("2K Scope (2.39:1)", 2048.0, 858.0),
    ("UHD 4K", 3840.0, 2160.0),
    ("4K DCI", 4096.0, 2160.0),
    ("4K Scope (2.39:1)", 4096.0, 1716.0),
    ("HD 720p", 1280.0, 720.0),
]

MEDIA_EXTENSIONS = (".exr", ".mov", ".mp4", ".mxf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".dpx")
SEQUENCE_EXTENSIONS = (".exr", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".dpx")
ASSET_3D_EXTENSIONS = (".abc", ".usd", ".usda", ".usdc", ".fbx", ".obj", ".vdb", ".bgeo", ".bgeo.sc", ".cube", ".3dl")

# --- Standard VFX Shot Departments ---
DEFAULT_SHOT_DEPARTMENTS = [
    {"name": "Compositing", "abbr": "cmp", "default_task": "comp"},
    {"name": "FX", "abbr": "fx", "default_task": "Effects"},
    {"name": "Lighting", "abbr": "lgt", "default_task": "Lighting"},
    {"name": "Animation", "abbr": "anm", "default_task": "Animation"},
    {"name": "Layout", "abbr": "lay", "default_task": "Layout"},
    {"name": "CharFX", "abbr": "cfx", "default_task": "CharacterEffects"},
]

# --- Modular Plugin & Standalone Configuration ---
USER_SETTINGS_PATH = os.path.join(PIPELINE_ROOT, "user_settings.json")
DEFAULT_ENABLED_DCCS = ["nuke", "houdini", "blender"]
DEFAULT_ENABLED_TOOLS = ["djv_player", "media_ingester", "slate_delivery"]
DEFAULT_RESOLVE_MARKER_FIELDS = ["status", "assign", "notes"]

# --- Fusion Persistent Data Key ---
FUSION_PREF_KEY = "VFXPipelineHub_Sessions"


def get_default_artist_name() -> str:
    """Return the current OS login username across Windows, macOS, and Linux."""
    return (os.environ.get("USERNAME") or os.environ.get("USER") or "Artist").strip() or "Artist"


def load_user_settings() -> dict:
    """Loads persistent user settings (enabled modules, custom executable paths, recent projects)."""
    import json
    defaults = {
        "enabled_dccs": list(DEFAULT_ENABLED_DCCS),
        "enabled_tools": list(DEFAULT_ENABLED_TOOLS),
        "dcc_paths": {},
        "dcc_env": {},
        "dcc_args": {},
        "dcc_default_bootstrap": True,
        "dcc_default_launch": True,
        "tool_paths": {},
        "recent_projects": [],
        "last_project_root": "",
        "default_projects_root": "",
        "ffmpeg_path": "",
        "ffprobe_path": "",
        "ocio_config": "",
        "artist_name": get_default_artist_name(),
        "studio_name": "",
        "slate_logo_path": "",
        "slate_disclaimer": DEFAULT_SLATE_DISCLAIMER,
        "accent_color": "#2383E2",
        "resolve_marker_fields": list(DEFAULT_RESOLVE_MARKER_FIELDS),
        "auto_save_excel": True,
        "github_repo": "",
        "check_updates_on_startup": True,
    }
    if os.path.isfile(USER_SETTINGS_PATH):
        try:
            with open(USER_SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
        except Exception as e:
            print(f"Warning loading user_settings.json: {e}")
    return defaults


def get_slate_logo_path() -> str:
    """
    Resolve the active slate logo PNG path:
      1. User-configured path in user_settings.json (`slate_logo_path`)
      2. Optional client logo placed at `assets/slate_logo.png`
      3. Empty string (neutral slate, no logo)
    """
    override = (load_user_settings().get("slate_logo_path") or "").strip()
    if override and os.path.isfile(override):
        return os.path.normpath(override)
    default_client_logo = os.path.join(PIPELINE_ASSETS_DIR, "slate_logo.png")
    if os.path.isfile(default_client_logo):
        return os.path.normpath(default_client_logo)
    return ""


def get_github_repo() -> str:
    """Return the configured GitHub 'owner/repo' slug (user_settings override -> config default)."""
    raw = (load_user_settings().get("github_repo") or GITHUB_REPO or "").strip()
    if not raw:
        return ""
    # Normalize full GitHub URLs like https://github.com/owner/repo(.git) -> owner/repo
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.strip().strip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    return raw


def get_ffmpeg_bin() -> str:
    """Resolve FFmpeg binary, honoring any user_settings.json override first."""
    override = (load_user_settings().get("ffmpeg_path") or "").strip()
    if override and os.path.isfile(override):
        return os.path.normpath(override)
    return FFMPEG_BIN


def get_ffprobe_bin() -> str:
    """Resolve FFprobe binary, honoring any user_settings.json override first."""
    override = (load_user_settings().get("ffprobe_path") or "").strip()
    if override and os.path.isfile(override):
        return os.path.normpath(override)
    return FFPROBE_BIN


def resolve_ocio_path(raw_path: str) -> str:
    """Resolve an OCIO config path (absolute, or relative to ocio/ or PIPELINE_ROOT)."""
    p = (raw_path or "").strip()
    if not p:
        return ""
    if os.path.isfile(p):
        return os.path.normpath(p)
    for base in (PIPELINE_OCIO_DIR, PIPELINE_ROOT):
        cand = os.path.join(base, p)
        if os.path.isfile(cand):
            return os.path.normpath(cand)
    return ""


def get_ocio_config(project_ocio_config: str = "") -> str:
    """
    Resolve the active OCIO config file path in priority order:
      1. Per-project override (project.json -> ocio_config)
      2. Global workstation override (user_settings.json -> ocio_config)
      3. Pipeline bundled default (ocio/fn-nuke_studio-config-v2.2.0_aces-v1.3_ocio-v2.4.ocio)
      4. Existing OS OCIO environment variable
    """
    for candidate in (
        project_ocio_config,
        load_user_settings().get("ocio_config", ""),
        DEFAULT_OCIO_CONFIG,
        os.environ.get("OCIO", ""),
    ):
        resolved = resolve_ocio_path(str(candidate or ""))
        if resolved:
            return resolved
    return ""


def list_bundled_ocio_configs() -> list:
    """Return sorted full paths to all .ocio config files bundled in PIPELINE_OCIO_DIR."""
    if not os.path.isdir(PIPELINE_OCIO_DIR):
        return []
    return sorted(
        os.path.normpath(os.path.join(PIPELINE_OCIO_DIR, f))
        for f in os.listdir(PIPELINE_OCIO_DIR)
        if f.lower().endswith(".ocio") and os.path.isfile(os.path.join(PIPELINE_OCIO_DIR, f))
    )


def save_user_settings(settings: dict) -> bool:
    """Saves persistent user settings to user_settings.json."""
    import json
    try:
        current = load_user_settings()
        current.update(settings)
        with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving user_settings.json: {e}")
        return False


def is_auto_save_excel_enabled() -> bool:
    """Return whether Auto-Save Excel is enabled in user_settings.json (default True)."""
    return bool(load_user_settings().get("auto_save_excel", True))


def set_auto_save_excel_enabled(enabled: bool) -> bool:
    """Persist the Auto-Save Excel toggle to user_settings.json."""
    return save_user_settings({"auto_save_excel": bool(enabled)})

