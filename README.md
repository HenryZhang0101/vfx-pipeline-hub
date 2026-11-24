# VFX Pipeline Workstation Hub — Quick Start Guide

A cross-platform (Windows & macOS) VFX project management, media review, DCC launching, and DaVinci Resolve pipeline workstation hub.

---

## 1. What's Included

- **VFX Project Manager** (`Launch_Project_Manager.bat` / `.command`): Shot & sequence tracker, 16:9 thumbnail previews, custom shot properties, multi-shot Media Manager, plate/asset ingester, DJV review & A/B compare, Slate + QuickTime Review/Delivery export, Excel import/export, and optional Google Sheets sync.
- **VFX DCC Launcher** (`Launch_DCC_Launcher.bat` / `.command`): Quick scene creation, versioning, and environment-configured launching for **Nuke / NukeX**, **Houdini**, and **Blender** (including Nuke 16+ Media Loader & Pipeline Write node).
- **DaVinci Resolve Utility Hub**: Runs inside DaVinci Resolve (`Workspace -> Scripts -> Utility -> VFX_Pipeline_Hub`) for timeline scanning, plate export (including *Render as Comp*), shot loading, and timeline marker sync.
- **Bundled Tools**: Portable Python + Qt runtime (Windows), FFmpeg & FFprobe (Windows), DJV media player (Windows), and ACES 1.3 Studio OCIO config.

---

## 2. Installation & First-Time Setup

### Windows (Zero-Install Portable)
1. Extract the release folder to a permanent location (e.g., `C:\Tools\VFX_Pipeline_Hub` or a shared studio drive).
2. Double-click **`Setup_Windows.bat`** to:
   - Verify bundled Python, Qt, Pillow, FFmpeg, DJV, and OCIO.
   - Install the **DaVinci Resolve Utility** script (`VFX_Pipeline_Hub.py`).
   - Optionally create **Desktop shortcuts** for Project Manager and DCC Launcher.
3. Launch using the Desktop shortcuts or **`Launch_Project_Manager.bat`** / **`Launch_DCC_Launcher.bat`**.

### macOS
1. Extract the release folder to a permanent location (e.g., `~/Applications/VFX_Pipeline_Hub`).
2. Ensure **Python 3.11+** and **FFmpeg** are installed:
   ```bash
   brew install python ffmpeg
   ```
3. If macOS Gatekeeper blocks downloaded `.command` scripts, clear the quarantine attribute once from Terminal:
   ```bash
   xattr -dr com.apple.quarantine /path/to/VFX_Pipeline_Hub
   chmod +x /path/to/VFX_Pipeline_Hub/*.command
   ```
4. Double-click **`Setup_macOS.command`** to create `.venv_mac`, install dependencies, install the DaVinci Resolve Utility script, and optionally create Desktop launchers.
5. Launch using **`Launch_Project_Manager.command`** or **`Launch_DCC_Launcher.command`**.

> **Note:** On macOS, install [DJV](https://darbyjohnston.github.io/DJV/) into `/Applications/DJV*.app` if you want A/B version comparison (otherwise media falls back to macOS QuickTime / Preview).

---

## 3. First-Run Configuration

Open **Settings -> App Settings** inside Project Manager (or DCC Launcher):

1. **Appearance & Identity**:
   - Set your **Artist Name** and **Accent Color**.
   - **Slate & Studio Branding**:
     - Set **Studio Name** (rendered on the review slate when no logo image is selected), **or**
     - Pick a **Slate Logo (PNG)** (transparent PNG recommended). You can also drop a logo file directly at `assets/slate_logo.png`.
     - Customize the **Slate Footer** disclaimer text if needed.
2. **Global Paths**:
   - Set **Default Projects Root** (e.g., `D:\VFX_Projects` or `/Volumes/VFX`) so the project browser opens there and auto-discovers VFX projects.
   - Optional overrides for FFmpeg, FFprobe, and OCIO config (blank = use bundled defaults).
3. **Settings -> DCC Settings**:
   - Verify auto-detected paths for Nuke, Houdini, and Blender, or browse to custom executables/arguments/environment variables.

---

## 4. DaVinci Resolve Integration

After running `Setup_Windows.bat` or `Setup_macOS.command`:
1. Open **DaVinci Resolve**.
2. Go to **Workspace -> Scripts -> Utility -> VFX_Pipeline_Hub**.
3. If you move the `VFX_Pipeline_Hub` folder to a different path later, simply re-run the Setup script to update the Resolve launcher.

---

## 5. Receiving Updates

VFX Pipeline Hub includes a built-in **GitHub Releases** updater:
1. Click **Updates** in the top-right header of Project Manager (or **Settings -> Check for Updates...**).
2. When a newer release is published, the button highlights automatically (`Update vX.Y.Z`).
3. Click **Download & Install Update** — only the lightweight Python code layer (~1–2 MB) is updated. Your `user_settings.json`, bundled `python/`, `lib/`, `bin/`, `.venv_mac/`, and custom `assets/slate_logo.png` are preserved, and a safety backup is saved in `_backup_prev/`.
4. Click **Restart Now** to relaunch on the new version.

---

## 6. Optional: Google Sheets Sync

To enable two-way Google Sheets shot tracking:
1. Create a Google Sheet and open **Extensions -> Apps Script**.
2. Paste the contents of `core/google_apps_script.js` into the Apps Script editor and deploy it as a **Web App** (Execute as: *Me*, Access: *Anyone with link* or your workspace).
3. Paste the deployed Web App URL into **Project -> Project Settings -> Google Sheet Webhook URL**.

---

## 7. Troubleshooting

- **Run diagnostics anytime**:
  - Windows: `python\python.exe -s setup_client.py --check-only`
  - macOS: `.venv_mac/bin/python3 setup_client.py --check-only`
- **Reset local workstation settings**: Close all apps and delete `user_settings.json` inside the installation folder — fresh defaults will be created on next launch.
- **DaVinci Resolve doesn't show `VFX_Pipeline_Hub`**: Ensure you ran `Setup_Windows.bat` / `Setup_macOS.command` and restart DaVinci Resolve. If your user account lacks admin rights for `ProgramData` / `/Library`, the setup script automatically installs to your user-level Fusion Scripts folder instead.
