"""Pure-Python project/shot context detection and output path builder for Nuke."""

from dataclasses import dataclass
import os
import re
from typing import Optional, Tuple

from config import DEFAULT_COMP_IDENTIFIER, DEFAULT_VERSION, load_user_settings
from core.models import Project
from core.storage import ProjectRepository
from core.timecode import format_shot_id, split_shot_id


VERSION_MODE_AUTO_NEXT = "Auto Next"
VERSION_MODE_MATCH_SCRIPT = "Match Script"
VERSION_MODE_MANUAL = "Manual"
VERSION_MODES = (
    VERSION_MODE_AUTO_NEXT,
    VERSION_MODE_MATCH_SCRIPT,
    VERSION_MODE_MANUAL,
)

MOVIE_FILE_TYPES = {"mov", "mp4", "mxf", "avi"}


@dataclass
class PipelineContext:
    """Resolved VFX Pipeline context for the active Nuke session."""

    vfx_root: str = ""
    project_name: str = ""
    seq: str = ""
    shot: str = ""
    shot_id: str = ""
    department: str = ""
    script_version: str = ""
    script_path: str = ""
    source: str = "none"  # "script", "env", "settings", "none"

    @property
    def is_valid_project(self) -> bool:
        return bool(self.vfx_root and os.path.isdir(self.vfx_root))

    @property
    def is_valid_shot(self) -> bool:
        return bool(self.is_valid_project and self.seq and self.shot)


def sanitize_identifier(identifier: str, default: str = DEFAULT_COMP_IDENTIFIER) -> str:
    """Sanitize a media identifier for safe folder/file naming."""
    raw = (identifier or "").strip()
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", raw).strip("_")
    return cleaned or default


def format_version(version_num: int) -> str:
    """Format an integer version number as v#### (clamped to >= 1)."""
    return f"v{max(1, int(version_num)):04d}"


def parse_version_number(version_str: str, default: int = 1) -> int:
    """Extract integer version number from a string like 'v0003'."""
    m = re.search(r"(\d+)", str(version_str or ""))
    return max(1, int(m.group(1))) if m else default


def find_project_root_for_path(path: str) -> str:
    """
    Walk up parent directories from `path` to locate the VFX project root
    containing `project.json` (or `_thumbnails`). Returns empty string if not found.
    """
    if not path:
        return ""
    cur = os.path.normpath(os.path.abspath(path))
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)

    while True:
        if os.path.isfile(os.path.join(cur, "project.json")):
            return cur
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        cur = parent
    return ""


def resolve_context_from_script_path(script_path: str) -> Optional[PipelineContext]:
    """
    Resolve Project, Sequence, Shot, Department, and Script Version from a Nuke
    script path following `<vfx_root>/<SEQ>/<SHOT>/scenes/<dept>/<file>_v####.nk`.
    """
    if not script_path or script_path.strip().lower() == "root":
        return None

    norm_script = os.path.normpath(os.path.abspath(script_path))
    vfx_root = find_project_root_for_path(norm_script)
    if not vfx_root:
        return None

    rel = os.path.relpath(norm_script, vfx_root)
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]

    seq = ""
    shot = ""
    shot_id = ""
    department = ""
    script_version = ""

    # Expected: <SEQ>/<SHOT>/scenes/<dept>/<filename>.nk (or <SEQ>/<SHOT>/...)
    if len(parts) >= 2 and not parts[0].startswith("_"):
        seq = parts[0]
        shot = parts[1]
        shot_id = format_shot_id(seq, shot)
    if len(parts) >= 4 and parts[2].lower() == "scenes":
        department = parts[3]

    fname = os.path.basename(norm_script)
    m_ver = re.search(r"_(v\d{3,5})(?:\.[^.]+)?$", fname, re.IGNORECASE)
    if m_ver:
        script_version = format_version(parse_version_number(m_ver.group(1)))

    repo = ProjectRepository()
    project = repo.load(vfx_root)

    return PipelineContext(
        vfx_root=vfx_root,
        project_name=project.name,
        seq=seq,
        shot=shot,
        shot_id=shot_id,
        department=department,
        script_version=script_version,
        script_path=norm_script,
        source="script",
    )


def get_current_nuke_script_path() -> str:
    """Return the active Nuke script path, or empty string if unsaved/outside Nuke."""
    try:
        import nuke  # type: ignore

        name = nuke.root().name()
        if name and name != "Root":
            return name
    except Exception:
        pass
    return ""


def resolve_active_context(script_path: Optional[str] = None) -> PipelineContext:
    """
    Resolve active VFX Pipeline context using priority chain:
      1. Active Nuke `.nk` script path (walking up to `project.json`)
      2. Launch environment variables (`VFX_PROJECT_ROOT` / `VFX_SHOT_ID`)
      3. `last_project_root` from `user_settings.json`
    """
    sp = script_path if script_path is not None else get_current_nuke_script_path()
    if sp:
        from_script = resolve_context_from_script_path(sp)
        if from_script and from_script.is_valid_project:
            return from_script

    env_root = (
        os.environ.get("VFX_PROJECT_ROOT")
        or os.environ.get("VFX_VFX_ROOT")
        or os.environ.get("VFX_PRISM_ROOT")
        or ""
    ).strip()
    if env_root and os.path.isdir(env_root):
        resolved_root = ProjectRepository.resolve_vfx_root(env_root)
        repo = ProjectRepository()
        project = repo.load(resolved_root)
        env_shot_id = os.environ.get("VFX_SHOT_ID", "").strip()
        env_seq = os.environ.get("VFX_SEQ", "").strip()
        env_shot = os.environ.get("VFX_SHOT", "").strip()
        if env_shot_id and not (env_seq and env_shot):
            env_seq, env_shot = split_shot_id(env_shot_id)
        shot_id = format_shot_id(env_seq, env_shot) if (env_seq and env_shot) else env_shot_id
        return PipelineContext(
            vfx_root=resolved_root,
            project_name=project.name or os.environ.get("VFX_PROJECT_NAME", ""),
            seq=env_seq,
            shot=env_shot,
            shot_id=shot_id,
            department=os.environ.get("VFX_DEPARTMENT", "").strip(),
            script_version=os.environ.get("VFX_SCENE_VERSION", "").strip(),
            script_path=sp,
            source="env",
        )

    settings = load_user_settings()
    last_root = (settings.get("last_project_root") or "").strip()
    if last_root and os.path.isdir(last_root):
        resolved_root = ProjectRepository.resolve_vfx_root(last_root)
        repo = ProjectRepository()
        project = repo.load(resolved_root)
        return PipelineContext(
            vfx_root=resolved_root,
            project_name=project.name,
            script_path=sp,
            source="settings",
        )

    return PipelineContext(script_path=sp, source="none")


def build_write_output_path(
    project: Project,
    repo: ProjectRepository,
    shot_id: str,
    identifier: str = DEFAULT_COMP_IDENTIFIER,
    version_mode: str = VERSION_MODE_AUTO_NEXT,
    manual_version: int = 1,
    script_version: str = "",
    file_type: str = "exr",
    frame_padding: str = "%04d",
) -> Tuple[str, str]:
    """
    Build the standardized Nuke Write output filepath under `<vfx_root>/<SEQ>/<SHOT>/media/<identifier>/<version>/`.

    Returns:
        (resolved_version, normalized_forward_slash_filepath)
    """
    seq, shot = split_shot_id(shot_id)
    if not project or not project.vfx_root or not seq or not shot:
        return (DEFAULT_VERSION, "")

    norm_shot_id = format_shot_id(seq, shot)
    clean_id = sanitize_identifier(identifier, default=DEFAULT_COMP_IDENTIFIER)
    ext = (file_type or "exr").strip().lstrip(".").lower() or "exr"

    mode = (version_mode or VERSION_MODE_AUTO_NEXT).strip()
    if mode == VERSION_MODE_MANUAL:
        resolved_ver = format_version(manual_version)
    elif mode == VERSION_MODE_MATCH_SCRIPT:
        resolved_ver = (
            format_version(parse_version_number(script_version))
            if script_version
            else DEFAULT_VERSION
        )
    else:
        # Auto Next: query next unused version folder on disk
        resolved_ver = repo.get_next_media_version(project, norm_shot_id, "media", clean_id)

    out_dir = os.path.join(
        repo.get_shot_dir(project, norm_shot_id),
        "media",
        clean_id,
        resolved_ver,
    )
    base_name = f"{norm_shot_id}_{clean_id}_{resolved_ver}"

    if ext in MOVIE_FILE_TYPES:
        filename = f"{base_name}.{ext}"
    else:
        filename = f"{base_name}.{frame_padding}.{ext}"

    full_path = os.path.join(out_dir, filename).replace("\\", "/")
    return resolved_ver, full_path
