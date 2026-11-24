"""
VFX Pipeline Workstation Hub — Cross-Platform Client Setup & Diagnostics

Run via Setup_Windows.bat (Windows) or Setup_macOS.command (macOS).
Supports:
  --check-only     Run environment diagnostics without modifying anything
  --yes            Non-interactive mode (accept defaults)
  --skip-resolve   Skip DaVinci Resolve Utility script installation
  --skip-shortcuts Skip Desktop shortcut creation
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
from typing import List, Tuple

PIPELINE_ROOT = os.path.dirname(os.path.abspath(__file__))
if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

import config


def _prompt_yes_no(question: str, default: bool = True, auto_yes: bool = False) -> bool:
    if auto_yes:
        print(f"{question} -> {'yes' if default else 'no'} (auto)")
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            ans = input(f"{question} {suffix}: ").strip().lower()
        except EOFError:
            return default
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def _resolve_utility_candidates() -> List[str]:
    if sys.platform == "win32":
        candidates = [
            r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility",
        ]
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(
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
        return candidates
    elif sys.platform == "darwin":
        return [
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
            os.path.expanduser(
                "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
            ),
        ]
    else:
        return [
            "/opt/resolve/Fusion/Scripts/Utility",
            os.path.expanduser("~/.local/share/DaVinciResolve/Fusion/Scripts/Utility"),
        ]


def run_diagnostics() -> Tuple[bool, List[str]]:
    """Verify Python dependencies, bundled media tools, and OCIO config."""
    issues: List[str] = []
    print("=" * 64)
    print(f"  VFX Pipeline Workstation Hub v{config.APP_VERSION} — Environment Check")
    print("=" * 64)
    print(f"  Install Root : {PIPELINE_ROOT}")
    print(f"  Python       : {sys.executable} ({sys.version.split()[0]})")
    print(f"  Platform     : {sys.platform}")
    print()

    required_mods = [
        ("PySide6", "Qt UI Framework"),
        ("qtpy", "Qt Abstraction Layer"),
        ("PIL", "Pillow (Thumbnails & Slates)"),
        ("openpyxl", "Excel Export/Import"),
        ("packaging", "Version Parsing"),
    ]
    print("[Python Packages]")
    for mod_name, label in required_mods:
        try:
            mod = __import__(mod_name)
            loc = getattr(mod, "__file__", "built-in")
            print(f"  [OK]   {label:30s} ({mod_name}) -> {os.path.dirname(loc) if loc else loc}")
        except Exception as e:
            print(f"  [FAIL] {label:30s} ({mod_name}) -> {e}")
            issues.append(f"Missing Python package: {mod_name}")

    print("\n[Media & Color Tools]")
    ffmpeg_bin = config.get_ffmpeg_bin()
    ffprobe_bin = config.get_ffprobe_bin()
    if os.path.isfile(ffmpeg_bin):
        print(f"  [OK]   FFmpeg   -> {ffmpeg_bin}")
    else:
        print(f"  [WARN] FFmpeg not found (expected {ffmpeg_bin} or on PATH)")
        if sys.platform == "darwin":
            print("         Install on macOS via Homebrew: brew install ffmpeg")
        issues.append("FFmpeg not found (required for EXR thumbnails and QT Review/Delivery)")

    if os.path.isfile(ffprobe_bin):
        print(f"  [OK]   FFprobe  -> {ffprobe_bin}")
    else:
        print(f"  [WARN] FFprobe not found")

    ocio_cfg = config.get_ocio_config()
    if ocio_cfg and os.path.isfile(ocio_cfg):
        print(f"  [OK]   OCIO     -> {ocio_cfg}")
    else:
        print("  [WARN] Bundled OCIO config not found")

    try:
        from tools.djv_tool import DJVPlayerTool

        djv = DJVPlayerTool()
        djv_exe = djv.get_executable()
        if djv_exe and os.path.isfile(djv_exe):
            print(f"  [OK]   DJV      -> {djv_exe}")
        else:
            print("  [INFO] DJV not found (optional — OS default player will be used as fallback)")
    except Exception as e:
        print(f"  [WARN] DJV check failed: {e}")

    print()
    return (len(issues) == 0), issues


def setup_macos_runtime() -> None:
    """On macOS, ensure .command scripts are executable and .venv_mac is initialized."""
    if sys.platform != "darwin":
        return

    print("[macOS Launcher Permissions]")
    for name in ("Launch_Project_Manager.command", "Launch_DCC_Launcher.command", "Setup_macOS.command"):
        path = os.path.join(PIPELINE_ROOT, name)
        if os.path.isfile(path):
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            print(f"  [OK] chmod +x {name}")

    venv_py = os.path.join(PIPELINE_ROOT, ".venv_mac", "bin", "python3")
    req_file = os.path.join(PIPELINE_ROOT, "requirements.txt")
    if not os.path.isfile(venv_py) and os.path.isfile(req_file):
        print("\n[macOS Virtual Environment]")
        print("  Creating .venv_mac and installing requirements.txt...")
        subprocess.check_call([sys.executable, "-m", "venv", os.path.join(PIPELINE_ROOT, ".venv_mac")])
        subprocess.check_call([venv_py, "-m", "pip", "install", "--upgrade", "pip"])
        subprocess.check_call([venv_py, "-m", "pip", "install", "-r", req_file])
        print("  [OK] .venv_mac ready")


def install_resolve_utility() -> bool:
    """Install VFX_Pipeline_Hub.py into DaVinci Resolve's Fusion/Scripts/Utility folder."""
    template_path = os.path.join(PIPELINE_ROOT, "integrations", "resolve", "VFX_Pipeline_Hub.py")
    if not os.path.isfile(template_path):
        print(f"  [ERROR] Template missing: {template_path}")
        return False

    with open(template_path, "r", encoding="utf-8") as f:
        rendered = f.read().replace("__PIPELINE_ROOT__", os.path.normpath(PIPELINE_ROOT))

    candidates = _resolve_utility_candidates()
    for target_dir in candidates:
        parent = os.path.dirname(target_dir)
        # Prefer an existing Resolve install folder; fall back to creating user-level folder
        if not os.path.isdir(target_dir) and not os.path.isdir(parent):
            continue
        try:
            os.makedirs(target_dir, exist_ok=True)
            dest = os.path.join(target_dir, "VFX_Pipeline_Hub.py")
            with open(dest, "w", encoding="utf-8") as f:
                f.write(rendered)
            print(f"  [OK] Installed Resolve Utility script -> {dest}")
            print("       In DaVinci Resolve: Workspace -> Scripts -> Utility -> VFX_Pipeline_Hub")
            return True
        except PermissionError:
            print(f"  [SKIP] No write permission for {target_dir} — trying user-level folder...")
        except OSError as e:
            print(f"  [SKIP] Could not write to {target_dir}: {e}")

    # Last resort: create the user-level folder even if Resolve hasn't been launched yet
    fallback_dir = candidates[-1]
    try:
        os.makedirs(fallback_dir, exist_ok=True)
        dest = os.path.join(fallback_dir, "VFX_Pipeline_Hub.py")
        with open(dest, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"  [OK] Installed Resolve Utility script (user-level) -> {dest}")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to install Resolve Utility script: {e}")
        return False


def create_desktop_shortcuts() -> bool:
    """Create Desktop shortcuts for Project Manager and DCC Launcher."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desktop):
        print(f"  [WARN] Desktop directory not found: {desktop}")
        return False

    if sys.platform == "win32":
        ico = config.APP_ICON_ICO if os.path.isfile(config.APP_ICON_ICO) else ""
        shortcuts = [
            ("VFX Project Manager.lnk", os.path.join(PIPELINE_ROOT, "Launch_Project_Manager.bat")),
            ("VFX DCC Launcher.lnk", os.path.join(PIPELINE_ROOT, "Launch_DCC_Launcher.bat")),
        ]
        ok_all = True
        for lnk_name, target_bat in shortcuts:
            lnk_path = os.path.join(desktop, lnk_name)
            ps_script = (
                "$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut('{lnk_path.replace(chr(39), chr(39)*2)}'); "
                f"$s.TargetPath = '{target_bat.replace(chr(39), chr(39)*2)}'; "
                f"$s.WorkingDirectory = '{PIPELINE_ROOT.replace(chr(39), chr(39)*2)}'; "
                + (f"$s.IconLocation = '{ico.replace(chr(39), chr(39)*2)}'; " if ico else "")
                + "$s.WindowStyle = 7; $s.Save()"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if proc.returncode == 0 and os.path.isfile(lnk_path):
                print(f"  [OK] Created Desktop shortcut -> {lnk_path}")
            else:
                print(f"  [WARN] Could not create {lnk_name}: {proc.stderr.strip()}")
                ok_all = False
        return ok_all

    elif sys.platform == "darwin":
        shortcuts = [
            ("VFX Project Manager.command", os.path.join(PIPELINE_ROOT, "Launch_Project_Manager.command")),
            ("VFX DCC Launcher.command", os.path.join(PIPELINE_ROOT, "Launch_DCC_Launcher.command")),
        ]
        for link_name, target_cmd in shortcuts:
            dest = os.path.join(desktop, link_name)
            try:
                if os.path.islink(dest) or os.path.exists(dest):
                    os.remove(dest)
                os.symlink(target_cmd, dest)
                print(f"  [OK] Created Desktop symlink -> {dest}")
            except OSError as e:
                print(f"  [WARN] Could not create {link_name}: {e}")
        return True

    print("  [INFO] Desktop shortcut creation is only implemented for Windows and macOS.")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="VFX Pipeline Workstation Hub Client Setup")
    parser.add_argument("--check-only", action="store_true", help="Only run environment diagnostics")
    parser.add_argument("--yes", "-y", action="store_true", help="Accept all prompts automatically")
    parser.add_argument("--skip-resolve", action="store_true", help="Skip DaVinci Resolve utility setup")
    parser.add_argument("--skip-shortcuts", action="store_true", help="Skip Desktop shortcut creation")
    args = parser.parse_args()

    if not args.check_only and sys.platform == "darwin":
        setup_macos_runtime()

    ok, issues = run_diagnostics()
    if args.check_only:
        if issues:
            print("Issues found:")
            for item in issues:
                print(f"  - {item}")
        else:
            print("All checks passed!")
        return 0 if ok else 1

    if not args.skip_resolve:
        print("[DaVinci Resolve Integration]")
        if _prompt_yes_no("Install VFX Pipeline Hub into DaVinci Resolve Scripts/Utility?", True, args.yes):
            install_resolve_utility()
        print()

    if not args.skip_shortcuts:
        print("[Desktop Shortcuts]")
        if _prompt_yes_no("Create Desktop shortcuts for Project Manager & DCC Launcher?", True, args.yes):
            create_desktop_shortcuts()
        print()

    print("=" * 64)
    if ok:
        print("  Setup complete! You can now launch VFX Project Manager or DCC Launcher.")
    else:
        print("  Setup finished with warnings (see above).")
    print("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
