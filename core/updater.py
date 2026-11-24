"""
UI-free GitHub Releases updater engine for VFX Pipeline Workstation Hub.

Checks a public (or token-authenticated) GitHub repository's Latest Release via
the GitHub REST API, downloads the lightweight code-layer zipball, backs up
managed files to `_backup_prev/`, applies the patch atomically while preserving
local settings & bundled runtimes, and supports clean process relaunch.
"""

from dataclasses import dataclass
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional, Tuple

from packaging.version import InvalidVersion, Version

from config import APP_VERSION, PIPELINE_ROOT, get_github_repo


# Files & folders managed by code-layer updates (everything else is strictly preserved)
MANAGED_ROOT_FILES = (
    "__init__.py",
    "config.py",
    "project_manager.py",
    "dcc_launcher.py",
    "standalone_hub.py",
    "init_project.py",
    "setup_client.py",
    "requirements.txt",
    "Launch_Project_Manager.bat",
    "Launch_DCC_Launcher.bat",
    "Setup_Windows.bat",
    "Launch_Project_Manager.command",
    "Launch_DCC_Launcher.command",
    "Setup_macOS.command",
    "README.md",
    "README_CLIENT.md",
)

MANAGED_DIRS = (
    "core",
    "dccs",
    "tools",
    "ui",
    "integrations",
    "ocio",
    os.path.join("assets", "icons"),
)

# Never overwrite or delete these paths during an update
PROTECTED_NAMES = {
    "user_settings.json",
    "python",
    "lib",
    "lib_mac",
    "bin",
    ".venv_mac",
    ".git",
    "_old",
    "dist",
    "_backup_prev",
}


@dataclass
class UpdateInfo:
    available: bool = False
    current_version: str = APP_VERSION
    latest_version: str = ""
    tag_name: str = ""
    release_name: str = ""
    release_notes: str = ""
    html_url: str = ""
    download_url: str = ""
    published_at: str = ""
    repo: str = ""
    error: str = ""


def _parse_version(raw: str) -> Optional[Version]:
    cleaned = (raw or "").strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    try:
        return Version(cleaned)
    except InvalidVersion:
        return None


def _build_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "User-Agent": f"VFXPipelineHub/{APP_VERSION}",
        "Accept": accept,
    }
    token = (os.environ.get("VFX_PIPELINE_GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_for_updates(
    repo: str = "",
    current_version: str = APP_VERSION,
    timeout: int = 10,
) -> UpdateInfo:
    """
    Query GitHub's Latest Release endpoint for `repo` (e.g. 'owner/repo')
    and compare against `current_version`.
    """
    resolved_repo = (repo or get_github_repo()).strip()
    info = UpdateInfo(current_version=current_version, repo=resolved_repo)
    if not resolved_repo or "/" not in resolved_repo:
        info.error = (
            "GitHub repository is not configured. "
            "Set 'GitHub Repo (owner/repo)' in Settings -> App Settings -> Global Paths."
        )
        return info

    api_url = f"https://api.github.com/repos/{resolved_repo}/releases/latest"
    req = urllib.request.Request(api_url, headers=_build_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            info.error = f"No published GitHub Releases found for '{resolved_repo}'."
        elif e.code == 403:
            info.error = "GitHub API rate limit exceeded. Please try again in a few minutes."
        else:
            info.error = f"GitHub API error (HTTP {e.code}): {e.reason}"
        return info
    except urllib.error.URLError as e:
        info.error = f"Unable to reach GitHub ({e.reason}). Check your internet connection."
        return info
    except Exception as e:
        info.error = f"Failed to check for updates: {e}"
        return info

    tag_name = str(payload.get("tag_name") or "").strip()
    latest_ver_str = tag_name[1:] if tag_name.lower().startswith("v") else tag_name
    info.tag_name = tag_name
    info.latest_version = latest_ver_str
    info.release_name = str(payload.get("name") or tag_name).strip()
    info.release_notes = str(payload.get("body") or "").strip()
    info.html_url = str(payload.get("html_url") or f"https://github.com/{resolved_repo}/releases").strip()
    info.published_at = str(payload.get("published_at") or "").strip()

    # Prefer an explicit code-patch asset if attached, otherwise use GitHub's zipball_url
    download_url = ""
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if "patch" in name and name.endswith(".zip"):
            download_url = str(asset.get("browser_download_url") or "")
            break
    if not download_url:
        download_url = str(payload.get("zipball_url") or "")
    info.download_url = download_url

    cur_v = _parse_version(current_version)
    rem_v = _parse_version(latest_ver_str)
    if cur_v is not None and rem_v is not None:
        info.available = rem_v > cur_v
    elif latest_ver_str and latest_ver_str != current_version:
        info.available = True
    else:
        info.available = False

    return info


def _find_extracted_root(extract_dir: str) -> Optional[str]:
    """Locate the directory containing config.py and project_manager.py inside an extracted archive."""
    if os.path.isfile(os.path.join(extract_dir, "config.py")) and os.path.isfile(
        os.path.join(extract_dir, "project_manager.py")
    ):
        return extract_dir
    for entry in sorted(os.listdir(extract_dir)):
        cand = os.path.join(extract_dir, entry)
        if (
            os.path.isdir(cand)
            and os.path.isfile(os.path.join(cand, "config.py"))
            and os.path.isfile(os.path.join(cand, "project_manager.py"))
        ):
            return cand
    return None


def _copy_managed_snapshot(src_root: str, dst_root: str) -> None:
    """Copy managed root files and managed directories from src_root into dst_root."""
    os.makedirs(dst_root, exist_ok=True)
    for fname in MANAGED_ROOT_FILES:
        src_f = os.path.join(src_root, fname)
        if os.path.isfile(src_f):
            shutil.copy2(src_f, os.path.join(dst_root, fname))
            # If README_CLIENT.md is in the repo, also refresh README.md in client installs
            if fname == "README_CLIENT.md" and not os.path.isfile(os.path.join(src_root, "README.md")):
                shutil.copy2(src_f, os.path.join(dst_root, "README.md"))

    for rel_dir in MANAGED_DIRS:
        src_d = os.path.join(src_root, rel_dir)
        dst_d = os.path.join(dst_root, rel_dir)
        if not os.path.isdir(src_d):
            continue
        if os.path.isdir(dst_d):
            shutil.rmtree(dst_d)
        os.makedirs(os.path.dirname(dst_d), exist_ok=True)
        shutil.copytree(
            src_d,
            dst_d,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
        )


def _normalize_command_scripts(install_root: str) -> None:
    """Ensure .command launchers have Unix LF line endings and executable permissions on macOS/Linux."""
    for fname in os.listdir(install_root):
        if not fname.endswith(".command"):
            continue
        fpath = os.path.join(install_root, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "rb") as f:
                raw = f.read()
            fixed = raw.replace(b"\r\n", b"\n")
            if fixed != raw:
                with open(fpath, "wb") as f:
                    f.write(fixed)
            if sys.platform != "win32":
                mode = os.stat(fpath).st_mode
                os.chmod(fpath, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            pass


def _resolve_utility_dirs() -> list:
    if sys.platform == "win32":
        dirs = [r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility"]
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            dirs.append(
                os.path.join(
                    appdata,
                    "Blackmagic Design",
                    "DaVinci Resolve",
                    "Support",
                    "Fusion",
                    "Scripts",
                    "Utility",
                )
            )
        return dirs
    elif sys.platform == "darwin":
        return [
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
            os.path.expanduser(
                "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
            ),
        ]
    return [
        "/opt/resolve/Fusion/Scripts/Utility",
        os.path.expanduser("~/.local/share/DaVinciResolve/Fusion/Scripts/Utility"),
    ]


def _refresh_installed_resolve_shims(
    install_root: str,
    utility_dirs: Optional[list] = None,
) -> int:
    """
    Best-effort refresh of any already-installed DaVinci Resolve Utility shim
    (`VFX_Pipeline_Hub.py`) from `<install_root>/integrations/resolve/VFX_Pipeline_Hub.py`.
    Never creates new Resolve installs and silently skips unwritable system folders.
    """
    template_path = os.path.join(install_root, "integrations", "resolve", "VFX_Pipeline_Hub.py")
    if not os.path.isfile(template_path):
        return 0
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            rendered = f.read().replace("__PIPELINE_ROOT__", os.path.normpath(install_root))
    except OSError:
        return 0

    refreshed = 0
    for util_dir in (utility_dirs if utility_dirs is not None else _resolve_utility_dirs()):
        if not util_dir:
            continue
        dest = os.path.join(util_dir, "VFX_Pipeline_Hub.py")
        if not os.path.isfile(dest):
            continue
        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(rendered)
            refreshed += 1
        except OSError:
            pass
    return refreshed


def download_and_apply_update(
    update_info: UpdateInfo,
    install_root: str = PIPELINE_ROOT,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    timeout: int = 60,
) -> Tuple[bool, str]:
    """
    Download `update_info.download_url`, validate the archive, back up current managed
    files to `<install_root>/_backup_prev/`, and apply the update safely.

    `progress_cb(downloaded_bytes, total_bytes, stage_message)` is optional.
    """
    url = (update_info.download_url or "").strip()
    if not url:
        return False, "No download URL available for this release."

    install_root = os.path.abspath(install_root)
    backup_dir = os.path.join(install_root, "_backup_prev")

    def _emit(done: int, total: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(done, total, msg)
            except Exception:
                pass

    with tempfile.TemporaryDirectory(prefix="vfx_hub_update_") as tmpdir:
        zip_path = os.path.join(tmpdir, "update.zip")
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # 1. Download archive (supports local file path for testing or https:// URL)
        _emit(0, 0, f"Downloading {update_info.tag_name or 'update'}...")
        try:
            if os.path.isfile(url):
                shutil.copy2(url, zip_path)
                total_sz = os.path.getsize(zip_path)
                _emit(total_sz, total_sz, "Archive loaded.")
            else:
                req = urllib.request.Request(url, headers=_build_headers(accept="*/*"))
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    total_sz = int(resp.headers.get("Content-Length") or 0)
                    downloaded = 0
                    with open(zip_path, "wb") as out_f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            out_f.write(chunk)
                            downloaded += len(chunk)
                            _emit(downloaded, total_sz, f"Downloading... {downloaded / 1024:.0f} KB")
        except Exception as e:
            return False, f"Failed to download update: {e}"

        # 2. Extract and validate
        _emit(0, 0, "Validating release archive...")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except Exception as e:
            return False, f"Corrupt update archive: {e}"

        src_root = _find_extracted_root(extract_dir)
        if not src_root:
            return False, "Invalid release archive: missing config.py or project_manager.py."

        # 3. Backup current managed files
        _emit(0, 0, "Backing up current installation...")
        try:
            if os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir)
            _copy_managed_snapshot(install_root, backup_dir)
        except Exception as e:
            return False, f"Failed to create safety backup: {e}"

        # 4. Apply new code layer (rollback automatically on failure)
        _emit(0, 0, "Applying update...")
        try:
            _copy_managed_snapshot(src_root, install_root)
            _normalize_command_scripts(install_root)
        except Exception as e:
            _emit(0, 0, "Update failed — rolling back...")
            try:
                _copy_managed_snapshot(backup_dir, install_root)
            except Exception:
                pass
            return False, f"Failed to apply update (rolled back to previous version): {e}"

        # 4b. Best-effort refresh of any already-installed DaVinci Resolve Utility shim
        _emit(0, 0, "Refreshing external integrations...")
        _refresh_installed_resolve_shims(install_root)

        # 5. On macOS .venv_mac, sync requirements.txt if present
        if sys.platform == "darwin":
            venv_py = os.path.join(install_root, ".venv_mac", "bin", "python3")
            req_file = os.path.join(install_root, "requirements.txt")
            if os.path.isfile(venv_py) and os.path.isfile(req_file):
                _emit(0, 0, "Syncing macOS Python requirements...")
                try:
                    subprocess.run(
                        [venv_py, "-m", "pip", "install", "-q", "-r", req_file],
                        cwd=install_root,
                        timeout=120,
                        check=False,
                    )
                except Exception:
                    pass

    ver_label = update_info.tag_name or update_info.latest_version or "latest"
    _emit(100, 100, f"Updated to {ver_label}!")
    return True, f"Successfully updated to {ver_label}. Restart to use the new version."


def relaunch_application(entry_script: str = "project_manager.py", extra_args: Optional[list] = None) -> bool:
    """Spawn a fresh detached instance of `entry_script` so the current app can exit cleanly."""
    script_path = (
        entry_script
        if os.path.isabs(entry_script)
        else os.path.join(PIPELINE_ROOT, entry_script)
    )
    if not os.path.isfile(script_path):
        return False

    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"

    if sys.platform == "win32":
        bundled_pyw = os.path.join(PIPELINE_ROOT, "python", "pythonw.exe")
        exe = bundled_pyw if os.path.isfile(bundled_pyw) else sys.executable
        cmd = [exe, "-s", script_path] + list(extra_args or [])
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, cwd=PIPELINE_ROOT, env=env, creationflags=flags)
    else:
        venv_py = os.path.join(PIPELINE_ROOT, ".venv_mac", "bin", "python3")
        exe = venv_py if os.path.isfile(venv_py) else sys.executable
        cmd = [exe, script_path] + list(extra_args or [])
        subprocess.Popen(cmd, cwd=PIPELINE_ROOT, env=env, start_new_session=True)
    return True
