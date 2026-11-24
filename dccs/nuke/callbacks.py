"""Nuke callback registration for Pipeline Write nodes and script events."""

from dccs.nuke.writer import (
    PIPELINE_WRITE_TRIGGER_KNOBS,
    is_pipeline_write,
    post_render_thumbnail,
    refresh_all_pipeline_writes,
    refresh_write_node,
)

_HEADLESS_REGISTERED = False
_GUI_REGISTERED = False


def on_write_knob_changed() -> None:
    """Auto-refresh Pipeline Write output path when identifier/version/file_type knobs change."""
    try:
        import nuke  # type: ignore

        node = nuke.thisNode()
        knob = nuke.thisKnob()
        if knob is None or knob.name() not in PIPELINE_WRITE_TRIGGER_KNOBS:
            return
        if is_pipeline_write(node):
            refresh_write_node(node)
    except Exception:
        pass


def on_before_render() -> None:
    """Refresh output path and ensure directories exist right before a Pipeline Write renders."""
    try:
        import nuke  # type: ignore

        node = nuke.thisNode()
        if is_pipeline_write(node):
            refresh_write_node(node, for_render=True)
    except Exception:
        pass


def on_after_render() -> None:
    """Update Project Manager thumbnail after a Pipeline Write finishes rendering."""
    try:
        import nuke  # type: ignore

        node = nuke.thisNode()
        if is_pipeline_write(node):
            post_render_thumbnail(node)
    except Exception:
        pass


def on_script_save() -> None:
    """When saving/versioning up a script, refresh Pipeline Writes in 'Match Script' mode."""
    try:
        refresh_all_pipeline_writes(only_match_script=True)
    except Exception:
        pass


def register_headless_callbacks() -> None:
    """Register callbacks safe for both GUI and terminal/farm renders (nuke -t)."""
    global _HEADLESS_REGISTERED
    if _HEADLESS_REGISTERED:
        return
    import nuke  # type: ignore

    nuke.addBeforeRender(on_before_render, nodeClass="Write")
    nuke.addAfterRender(on_after_render, nodeClass="Write")
    _HEADLESS_REGISTERED = True


def register_gui_callbacks() -> None:
    """Register interactive GUI callbacks (knobChanged + onScriptSave)."""
    global _GUI_REGISTERED
    if _GUI_REGISTERED:
        return
    import nuke  # type: ignore

    nuke.addKnobChanged(on_write_knob_changed, nodeClass="Write")
    nuke.addOnScriptSave(on_script_save)
    _GUI_REGISTERED = True
