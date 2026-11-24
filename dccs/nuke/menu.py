"""Foundry Nuke menu.py — GUI menu, toolbar, and interactive callback registration."""

import nuke  # type: ignore


def _show_shot_context() -> None:
    from dccs.nuke.context import resolve_active_context

    ctx = resolve_active_context()
    lines = [
        f"Source: {ctx.source}",
        f"Project: {ctx.project_name or '(none)'}",
        f"VFX Root: {ctx.vfx_root or '(none)'}",
        f"Shot ID: {ctx.shot_id or '(none)'}",
        f"Department: {ctx.department or '(none)'}",
        f"Script Version: {ctx.script_version or '(unsaved)'}",
    ]
    nuke.message("\n".join(lines))


def _refresh_all_writes_ui() -> None:
    from dccs.nuke.writer import refresh_all_pipeline_writes

    count = refresh_all_pipeline_writes()
    nuke.tprint(f"[VFX Pipeline] Refreshed {count} Pipeline Write node(s).")


def _register_menus() -> None:
    from dccs.nuke.callbacks import register_gui_callbacks

    register_gui_callbacks()

    top_menu = nuke.menu("Nuke")
    p_menu = top_menu.addMenu("VFX Pipeline")
    p_menu.addCommand(
        "Media Loader...",
        "from dccs.nuke.media_loader import show_media_loader; show_media_loader()",
    )
    p_menu.addSeparator()
    p_menu.addCommand(
        "Create Pipeline Write",
        "from dccs.nuke.writer import create_pipeline_write; create_pipeline_write()",
    )
    p_menu.addCommand(
        "Convert Selected Write to Pipeline Write",
        "from dccs.nuke.writer import convert_selected_writes; convert_selected_writes()",
    )
    p_menu.addCommand(
        "Refresh All Pipeline Writes",
        "from dccs.nuke.menu import _refresh_all_writes_ui; _refresh_all_writes_ui()",
    )
    p_menu.addSeparator()
    p_menu.addCommand(
        "Update Selected Reads to Latest",
        "from dccs.nuke.media_loader import update_selected_reads_to_latest; update_selected_reads_to_latest()",
    )
    p_menu.addSeparator()
    p_menu.addCommand(
        "Show Shot Context",
        "from dccs.nuke.menu import _show_shot_context; _show_shot_context()",
    )

    # Also register on the Nodes toolbar for quick Tab/sidebar access
    nodes_tb = nuke.toolbar("Nodes")
    tb_menu = nodes_tb.addMenu("VFX Pipeline")
    tb_menu.addCommand(
        "Media Loader...",
        "from dccs.nuke.media_loader import show_media_loader; show_media_loader()",
    )
    tb_menu.addCommand(
        "Pipeline Write",
        "from dccs.nuke.writer import create_pipeline_write; create_pipeline_write()",
    )
    tb_menu.addCommand(
        "Update Selected Reads to Latest",
        "from dccs.nuke.media_loader import update_selected_reads_to_latest; update_selected_reads_to_latest()",
    )


if nuke.env.get("gui"):
    try:
        _register_menus()
    except Exception as _exc:
        nuke.tprint(f"[VFX Pipeline] Warning during Nuke menu.py: {_exc}")
