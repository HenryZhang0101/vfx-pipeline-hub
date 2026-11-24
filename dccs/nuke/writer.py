"""Pipeline Writer utilities for Nuke: vanilla Write node augmented with a Pipeline tab."""

import os
from typing import List, Optional, Set, Tuple

from config import DEFAULT_COMP_IDENTIFIER, open_path_in_os
from core.storage import ProjectRepository
from core.thumbnails import generate_media_thumbnail
from core.timecode import format_shot_id, split_shot_id
from dccs.nuke.context import (
    PipelineContext,
    VERSION_MODE_AUTO_NEXT,
    VERSION_MODE_MANUAL,
    VERSION_MODE_MATCH_SCRIPT,
    VERSION_MODES,
    build_write_output_path,
    parse_version_number,
    resolve_active_context,
    sanitize_identifier,
)

# Guard against recursive knobChanged calls while updating knobs programmatically
_UPDATING_NODES: Set[str] = set()

PIPELINE_WRITE_MARKER = "vfx_pipeline_write"
PIPELINE_WRITE_TRIGGER_KNOBS = {
    "vfx_shot_id",
    "vfx_identifier",
    "vfx_version_mode",
    "vfx_version_num",
    "file_type",
}
PIPELINE_WRITE_LABEL = (
    "[value vfx_identifier] ([value vfx_resolved_version])\n"
    "[file tail [value file]]"
)


def _tprint(msg: str) -> None:
    try:
        import nuke  # type: ignore

        nuke.tprint(f"[VFX Pipeline] {msg}")
    except Exception:
        print(f"[VFX Pipeline] {msg}")


def is_pipeline_write(node) -> bool:
    """Return True if `node` is a Nuke Write node with our Pipeline tab marker."""
    if node is None:
        return False
    try:
        if node.Class() != "Write":
            return False
        marker = node.knob(PIPELINE_WRITE_MARKER)
        return bool(marker and marker.value())
    except Exception:
        return False


def _add_knob_if_missing(node, knob) -> None:
    if node.knob(knob.name()) is None:
        node.addKnob(knob)


def setup_pipeline_write_node(
    node,
    identifier: str = DEFAULT_COMP_IDENTIFIER,
    version_mode: str = VERSION_MODE_AUTO_NEXT,
    shot_id: str = "",
    ctx: Optional[PipelineContext] = None,
) -> bool:
    """
    Idempotently add the 'Pipeline' user tab and knobs to a vanilla Nuke Write node,
    then refresh its output path from the active pipeline context.
    """
    import nuke  # type: ignore

    if node is None or node.Class() != "Write":
        return False

    active_ctx = ctx or resolve_active_context()
    resolved_shot = (shot_id or active_ctx.shot_id or "").strip()
    clean_id = sanitize_identifier(identifier, default=DEFAULT_COMP_IDENTIFIER)
    mode = version_mode if version_mode in VERSION_MODES else VERSION_MODE_AUTO_NEXT

    if node.knob("pipeline_tab") is None:
        tab = nuke.Tab_Knob("pipeline_tab", "Pipeline")
        node.addKnob(tab)

    if node.knob(PIPELINE_WRITE_MARKER) is None:
        mk = nuke.Boolean_Knob(PIPELINE_WRITE_MARKER, "Pipeline Write")
        mk.setValue(True)
        mk.setFlag(nuke.INVISIBLE)
        node.addKnob(mk)
    else:
        node.knob(PIPELINE_WRITE_MARKER).setValue(True)

    if node.knob("vfx_shot_id") is None:
        k_shot = nuke.String_Knob("vfx_shot_id", "Shot ID")
        k_shot.setTooltip("Target Shot ID (e.g. R01-0010). Auto-filled from active Nuke script/context.")
        k_shot.setValue(resolved_shot)
        node.addKnob(k_shot)
    elif resolved_shot and not node.knob("vfx_shot_id").value():
        node.knob("vfx_shot_id").setValue(resolved_shot)

    if node.knob("vfx_identifier") is None:
        k_ident = nuke.String_Knob("vfx_identifier", "Identifier")
        k_ident.setTooltip("Media identifier subfolder (e.g. comp, precomp, roto, slapcomp).")
        k_ident.setValue(clean_id)
        node.addKnob(k_ident)

    if node.knob("vfx_version_mode") is None:
        k_mode = nuke.Enumeration_Knob("vfx_version_mode", "Version Mode", list(VERSION_MODES))
        k_mode.setTooltip(
            "Auto Next: next unused version folder on disk.\n"
            "Match Script: matches the current .nk script version.\n"
            "Manual: uses the Manual Version number below."
        )
        k_mode.setValue(mode)
        node.addKnob(k_mode)

    if node.knob("vfx_version_num") is None:
        k_vnum = nuke.Int_Knob("vfx_version_num", "Manual Version")
        k_vnum.setTooltip("Version number used when Version Mode is set to Manual.")
        k_vnum.setValue(1)
        node.addKnob(k_vnum)

    if node.knob("vfx_resolved_version") is None:
        k_res = nuke.String_Knob("vfx_resolved_version", "Resolved Version")
        k_res.setTooltip("Active resolved output version (v####).")
        k_res.setValue("v0001")
        node.addKnob(k_res)

    _add_knob_if_missing(node, nuke.Text_Knob("vfx_div1", ""))

    if node.knob("vfx_refresh_btn") is None:
        b_ref = nuke.PyScript_Knob(
            "vfx_refresh_btn",
            "Refresh Path",
            "from dccs.nuke.writer import refresh_write_node; refresh_write_node(nuke.thisNode())",
        )
        b_ref.setFlag(nuke.STARTLINE)
        node.addKnob(b_ref)

    if node.knob("vfx_next_ver_btn") is None:
        b_next = nuke.PyScript_Knob(
            "vfx_next_ver_btn",
            "Next Version (+1)",
            "from dccs.nuke.writer import bump_write_next_version; bump_write_next_version(nuke.thisNode())",
        )
        node.addKnob(b_next)

    if node.knob("vfx_open_dir_btn") is None:
        b_open = nuke.PyScript_Knob(
            "vfx_open_dir_btn",
            "Open Folder",
            "from dccs.nuke.writer import open_write_folder; open_write_folder(nuke.thisNode())",
        )
        node.addKnob(b_open)

    if node.knob("vfx_create_read_btn") is None:
        b_read = nuke.PyScript_Knob(
            "vfx_create_read_btn",
            "Create Read",
            "from dccs.nuke.writer import create_read_from_write; create_read_from_write(nuke.thisNode())",
        )
        node.addKnob(b_read)

    _add_knob_if_missing(node, nuke.Text_Knob("vfx_div2", ""))

    if node.knob("vfx_auto_thumb") is None:
        k_thumb = nuke.Boolean_Knob("vfx_auto_thumb", "Update Project Manager Thumbnail on Render")
        k_thumb.setFlag(nuke.STARTLINE)
        k_thumb.setValue(True)
        node.addKnob(k_thumb)

    # Ensure standard Write conveniences
    try:
        if node.knob("create_directories"):
            node.knob("create_directories").setValue(True)
    except Exception:
        pass

    try:
        if node.knob("label"):
            node.knob("label").setValue(PIPELINE_WRITE_LABEL)
    except Exception:
        pass

    refresh_write_node(node, ctx=active_ctx)
    return True


def create_pipeline_write(
    identifier: str = DEFAULT_COMP_IDENTIFIER,
    version_mode: str = VERSION_MODE_AUTO_NEXT,
):
    """Create a new vanilla Nuke Write node pre-configured with the Pipeline tab."""
    import nuke  # type: ignore

    undo = nuke.Undo()
    undo.begin("Create Pipeline Write")
    try:
        node = nuke.createNode("Write")
        try:
            node.knob("channels").setValue("rgba")
            node.knob("file_type").setValue("exr")
            if node.knob("datatype"):
                node.knob("datatype").setValue("16 bit half")
            if node.knob("compression"):
                node.knob("compression").setValue("Zip (1 scanline)")
            if node.knob("create_directories"):
                node.knob("create_directories").setValue(True)
        except Exception:
            pass

        setup_pipeline_write_node(node, identifier=identifier, version_mode=version_mode)
        return node
    finally:
        undo.end()


def convert_selected_writes() -> int:
    """Add the Pipeline tab to all currently selected vanilla Write nodes."""
    import nuke  # type: ignore

    selected = [n for n in nuke.selectedNodes("Write")]
    if not selected:
        nuke.message("Please select one or more Write nodes to convert.")
        return 0

    ctx = resolve_active_context()
    count = 0
    undo = nuke.Undo()
    undo.begin("Convert Write to Pipeline Write")
    try:
        for node in selected:
            if setup_pipeline_write_node(node, ctx=ctx):
                count += 1
    finally:
        undo.end()

    _tprint(f"Converted {count} Write node(s) to Pipeline Write.")
    return count


def refresh_write_node(
    node,
    for_render: bool = False,
    ctx: Optional[PipelineContext] = None,
) -> Tuple[str, str]:
    """
    Recompute and apply the auto-generated output filepath on a Pipeline Write node.

    Returns:
        (resolved_version, output_filepath)
    """
    if not is_pipeline_write(node):
        return ("", "")

    node_key = ""
    try:
        node_key = node.fullName()
    except Exception:
        node_key = str(id(node))

    if node_key in _UPDATING_NODES:
        return ("", "")

    _UPDATING_NODES.add(node_key)
    try:
        active_ctx = ctx or resolve_active_context()
        if not active_ctx.is_valid_project:
            _tprint(
                f"{node.name()}: No active VFX project detected. "
                "Save the .nk script inside a project or launch Nuke via DCC Launcher."
            )
            return ("", "")

        shot_id = ""
        if node.knob("vfx_shot_id"):
            shot_id = str(node.knob("vfx_shot_id").value() or "").strip()
        if not shot_id:
            shot_id = active_ctx.shot_id
            if shot_id and node.knob("vfx_shot_id"):
                node.knob("vfx_shot_id").setValue(shot_id)

        if not shot_id:
            _tprint(f"{node.name()}: Shot ID is empty; cannot build output path.")
            return ("", "")

        raw_ident = node.knob("vfx_identifier").value() if node.knob("vfx_identifier") else DEFAULT_COMP_IDENTIFIER
        clean_ident = sanitize_identifier(str(raw_ident or ""), default=DEFAULT_COMP_IDENTIFIER)
        mode = (
            str(node.knob("vfx_version_mode").value())
            if node.knob("vfx_version_mode")
            else VERSION_MODE_AUTO_NEXT
        )
        manual_num = int(node.knob("vfx_version_num").value()) if node.knob("vfx_version_num") else 1

        # Toggle Manual Version knob enabled state
        if node.knob("vfx_version_num"):
            node.knob("vfx_version_num").setEnabled(mode == VERSION_MODE_MANUAL)

        file_type = "exr"
        if node.knob("file_type"):
            ft = str(node.knob("file_type").value() or "").strip()
            if ft:
                file_type = ft

        repo = ProjectRepository()
        project = repo.load(active_ctx.vfx_root)
        resolved_ver, out_path = build_write_output_path(
            project=project,
            repo=repo,
            shot_id=shot_id,
            identifier=clean_ident,
            version_mode=mode,
            manual_version=manual_num,
            script_version=active_ctx.script_version,
            file_type=file_type,
        )

        if out_path:
            if node.knob("file"):
                node.knob("file").setValue(out_path)
            if node.knob("vfx_resolved_version"):
                node.knob("vfx_resolved_version").setValue(resolved_ver)
            if mode != VERSION_MODE_MANUAL and node.knob("vfx_version_num"):
                node.knob("vfx_version_num").setValue(parse_version_number(resolved_ver))
            if for_render:
                out_dir = os.path.dirname(out_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)

        return resolved_ver, out_path
    finally:
        _UPDATING_NODES.discard(node_key)


def refresh_all_pipeline_writes(only_match_script: bool = False) -> int:
    """Refresh all Pipeline Write nodes in the current Nuke script."""
    import nuke  # type: ignore

    ctx = resolve_active_context()
    updated = 0
    for node in nuke.allNodes("Write", recurseGroups=True):
        if not is_pipeline_write(node):
            continue
        if only_match_script:
            mode = str(node.knob("vfx_version_mode").value()) if node.knob("vfx_version_mode") else ""
            if mode != VERSION_MODE_MATCH_SCRIPT:
                continue
        _, path = refresh_write_node(node, ctx=ctx)
        if path:
            updated += 1
    return updated


def bump_write_next_version(node) -> Tuple[str, str]:
    """
    Advance the Write node to the next unused version on disk (or +1 above current
    resolved version, whichever is higher) and switch to Manual mode to lock it.
    """
    if not is_pipeline_write(node):
        return ("", "")

    ctx = resolve_active_context()
    if not ctx.is_valid_project:
        return ("", "")

    shot_id = str(node.knob("vfx_shot_id").value() or "").strip() or ctx.shot_id
    ident = sanitize_identifier(
        str(node.knob("vfx_identifier").value() if node.knob("vfx_identifier") else ""),
        default=DEFAULT_COMP_IDENTIFIER,
    )
    current_ver_str = (
        str(node.knob("vfx_resolved_version").value())
        if node.knob("vfx_resolved_version")
        else "v0001"
    )
    current_num = parse_version_number(current_ver_str, default=1)

    repo = ProjectRepository()
    project = repo.load(ctx.vfx_root)
    disk_next_str = repo.get_next_media_version(project, shot_id, "media", ident)
    disk_next_num = parse_version_number(disk_next_str, default=1)
    target_num = max(disk_next_num, current_num + 1)

    if node.knob("vfx_version_mode"):
        node.knob("vfx_version_mode").setValue(VERSION_MODE_MANUAL)
    if node.knob("vfx_version_num"):
        node.knob("vfx_version_num").setEnabled(True)
        node.knob("vfx_version_num").setValue(target_num)

    return refresh_write_node(node, ctx=ctx)


def open_write_folder(node) -> None:
    """Open the Write node's output directory (or nearest existing parent) in the OS file browser."""
    import nuke  # type: ignore

    file_val = str(node.knob("file").value() or "").strip() if node and node.knob("file") else ""
    if not file_val:
        nuke.message("Write node has no output filepath yet.")
        return

    target = os.path.dirname(os.path.normpath(file_val))
    while target and not os.path.isdir(target):
        parent = os.path.dirname(target)
        if not parent or parent == target:
            break
        target = parent

    if target and os.path.isdir(target):
        open_path_in_os(target)
    else:
        nuke.message(f"Output directory does not exist yet:\n{os.path.dirname(file_val)}")


def create_read_from_write(node):
    """Create a Nuke Read node beside the Write node pointing to its rendered output."""
    import nuke  # type: ignore

    if node is None or not node.knob("file"):
        return None

    file_val = str(node.knob("file").value() or "").strip()
    if not file_val:
        nuke.message("Write node has no output filepath.")
        return None

    out_dir = os.path.dirname(os.path.normpath(file_val))
    repo = ProjectRepository()
    inspected = repo.inspect_media_folder(out_dir) if os.path.isdir(out_dir) else None

    if inspected:
        read_pattern = inspected["nuke_pattern"]
        first_f = int(inspected["start_frame"])
        last_f = int(inspected["end_frame"])
    else:
        read_pattern = file_val.replace("\\", "/")
        use_limit = bool(node.knob("use_limit").value()) if node.knob("use_limit") else False
        if use_limit and node.knob("first") and node.knob("last"):
            first_f = int(node.knob("first").value())
            last_f = int(node.knob("last").value())
        else:
            first_f = int(nuke.root().firstFrame())
            last_f = int(nuke.root().lastFrame())

    shot_id = str(node.knob("vfx_shot_id").value() or "") if node.knob("vfx_shot_id") else ""
    ident = str(node.knob("vfx_identifier").value() or DEFAULT_COMP_IDENTIFIER) if node.knob("vfx_identifier") else DEFAULT_COMP_IDENTIFIER
    version = str(node.knob("vfx_resolved_version").value() or "v0001") if node.knob("vfx_resolved_version") else "v0001"

    undo = nuke.Undo()
    undo.begin("Create Read from Pipeline Write")
    try:
        read_node = nuke.nodes.Read(
            file=read_pattern,
            first=first_f,
            last=last_f,
            origfirst=first_f,
            origlast=last_f,
        )
        read_node.setXpos(int(node.xpos()) + 140)
        read_node.setYpos(int(node.ypos()))
        read_node.knob("label").setValue(f"{ident} ({version})\n[value first]-[value last]")

        from dccs.nuke.media_loader import stamp_read_pipeline_knobs

        stamp_read_pipeline_knobs(
            read_node,
            shot_id=shot_id,
            category="media",
            identifier=ident,
            version=version,
        )
        return read_node
    finally:
        undo.end()


def post_render_thumbnail(node) -> Optional[str]:
    """Generate/update the Project Manager media thumbnail after a Pipeline Write finishes rendering."""
    if not is_pipeline_write(node):
        return None
    if node.knob("vfx_auto_thumb") and not node.knob("vfx_auto_thumb").value():
        return None

    ctx = resolve_active_context()
    if not ctx.is_valid_project:
        return None

    file_val = str(node.knob("file").value() or "").strip() if node.knob("file") else ""
    if not file_val:
        return None

    out_dir = os.path.dirname(os.path.normpath(file_val))
    if not os.path.isdir(out_dir):
        return None

    shot_id = str(node.knob("vfx_shot_id").value() or "").strip() or ctx.shot_id
    seq, shot = split_shot_id(shot_id)
    if not (seq and shot):
        return None
    norm_shot_id = format_shot_id(seq, shot)
    ident = sanitize_identifier(
        str(node.knob("vfx_identifier").value() if node.knob("vfx_identifier") else ""),
        default=DEFAULT_COMP_IDENTIFIER,
    )
    version = str(node.knob("vfx_resolved_version").value() or "v0001") if node.knob("vfx_resolved_version") else "v0001"

    try:
        repo = ProjectRepository()
        project = repo.load(ctx.vfx_root)
        inspected = repo.inspect_media_folder(out_dir)
        if not inspected:
            return None
        media_item = {
            "shot_id": norm_shot_id,
            "category": "media",
            "identifier": ident,
            "version": version,
            "version_dir": out_dir,
            **inspected,
        }
        ok, thumb_path = generate_media_thumbnail(project, media_item, force=True)
        if ok:
            _tprint(f"Updated Project Manager thumbnail: {thumb_path}")
            return thumb_path
    except Exception as exc:
        _tprint(f"Thumbnail generation skipped: {exc}")
    return None
