import os
import re
import glob
import sys
from typing import Dict, List, Tuple, Any

from dccs.base_dcc import BaseDCCPlugin


class NukeDCCPlugin(BaseDCCPlugin):
    """
    Modular Nuke DCC Plugin (Nuke 16 / 17+).
    Includes Smart .nk Script Auto-Bootstrapper (auto-creates Read nodes for all shot
    plates, configures Root frame range / FPS / format, and wires a pipeline Write node)
    and automatic Write-node version-up updating.
    """

    dcc_id = "nuke"
    display_name = "Foundry Nuke"
    badge_color = "#f5a623"
    extensions = (".nk",)
    default_department = "cmp"
    default_task = "comp"

    def auto_detect_executables(self) -> List[str]:
        found: List[str] = []
        if sys.platform == "win32":
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            pattern = os.path.join(program_files, "Nuke*")
            nuke_dirs = sorted(glob.glob(pattern), reverse=True)
            for d in nuke_dirs:
                if not os.path.isdir(d):
                    continue
                for entry in sorted(os.listdir(d), reverse=True):
                    if re.match(r"^Nuke\d+\.\d+\.exe$", entry, re.IGNORECASE):
                        full = os.path.join(d, entry)
                        if os.path.isfile(full):
                            found.append(os.path.normpath(full))
        elif sys.platform == "darwin":
            for d in sorted(glob.glob("/Applications/Nuke*"), reverse=True):
                for app_path in sorted(glob.glob(os.path.join(d, "Nuke*.app")), reverse=True):
                    macos_dir = os.path.join(app_path, "Contents", "MacOS")
                    if os.path.isdir(macos_dir):
                        for entry in sorted(os.listdir(macos_dir), reverse=True):
                            if re.match(r"^Nuke\d+\.\d+$", entry, re.IGNORECASE):
                                full = os.path.join(macos_dir, entry)
                                if os.path.isfile(full):
                                    found.append(os.path.normpath(full))
        return found

    def build_environment(self, shot_context: Dict[str, Any]) -> Dict[str, str]:
        env = super().build_environment(shot_context)
        from config import PIPELINE_ROOT

        env["VFX_PIPELINE_HOST_DCC"] = "nuke"
        nuke_plugin_dir = os.path.join(PIPELINE_ROOT, "dccs", "nuke").replace("\\", "/")
        existing = env.get("NUKE_PATH", "")
        parts = [p for p in existing.split(os.pathsep) if p]
        norm_existing = {os.path.normcase(os.path.normpath(p)) for p in parts}
        if os.path.normcase(os.path.normpath(nuke_plugin_dir)) not in norm_existing:
            parts.insert(0, nuke_plugin_dir)
        env["NUKE_PATH"] = os.pathsep.join(parts)
        return env

    def build_launch_command(self, exe: str, scene_filepath: str, shot_context: Dict[str, Any]) -> List[str]:
        # Launch NukeX by default unless another Nuke mode flag is specified in extra_args
        extra_lower = {a.strip().lower() for a in self._extra_args}
        mode_flags = {"--nukex", "--studio", "--nukeassist", "--indie", "--nc", "--nukenc"}
        cmd = [exe]
        if not (extra_lower & mode_flags):
            cmd.append("--nukex")
        cmd.append(os.path.normpath(scene_filepath))
        return cmd

    def create_scene(
        self,
        scene_filepath: str,
        shot_context: Dict[str, Any],
        bootstrap: bool = True,
    ) -> Tuple[bool, str]:
        """
        Generates a bootstrapped Nuke (.nk) script containing:
          - Root node with shot frame range (e.g., 1001-1065), FPS, and resolution format
          - Read nodes for all ingested shot media plates (plate, bg, fg, etc.)
          - Pre-configured Pipeline Write_Comp node pointing to <vfx_root>/<SEQ>/<SHOT>/media/comp/<version>/
        """
        try:
            os.makedirs(os.path.dirname(scene_filepath), exist_ok=True)
            nk_text = self._generate_bootstrapped_nk(scene_filepath, shot_context, include_nodes=bootstrap)
            with open(scene_filepath, "w", encoding="utf-8") as f:
                f.write(nk_text)
            plates_count = len(shot_context.get("plates", []))
            msg = f"Created Nuke script {os.path.basename(scene_filepath)} (bootstrapped with {plates_count} plate(s) & Pipeline Write node)"
            return True, msg
        except Exception as e:
            return False, f"Failed to create Nuke script: {e}"

    def version_up_scene(
        self,
        source_filepath: str,
        new_filepath: str,
        shot_context: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Copies source .nk to new version and automatically updates Write node output paths
        from old version (e.g. v0001) to new version (e.g. v0002).
        """
        if not os.path.isfile(source_filepath):
            return False, f"Source Nuke script not found: {source_filepath}"

        try:
            os.makedirs(os.path.dirname(new_filepath), exist_ok=True)
            with open(source_filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            new_ver = shot_context.get("version", "")
            m_old = re.search(r"_(v\d{3,5})\.nk$", os.path.basename(source_filepath), re.IGNORECASE)
            old_ver = m_old.group(1) if m_old else ""

            if old_ver and new_ver and old_ver != new_ver:
                # Update render output paths in core/ media layout
                content = re.sub(
                    rf"(/media/[^/\s\"']+/)({re.escape(old_ver)})(/)",
                    rf"\g<1>{new_ver}\g<3>",
                    content,
                )
                # Backwards compatibility: legacy Prism render output paths
                content = re.sub(
                    rf"(Renders/2dRender/[^/\s\"']+/)({re.escape(old_ver)})(/)",
                    rf"\g<1>{new_ver}\g<3>",
                    content,
                )
                content = re.sub(
                    rf"(_comp_)({re.escape(old_ver)})([._])",
                    rf"\g<1>{new_ver}\g<3>",
                    content,
                )
                content = re.sub(
                    rf"(\bvfx_resolved_version\s+\"?)({re.escape(old_ver)})(\"?)(\s)",
                    rf"\g<1>{new_ver}\g<3>\g<4>",
                    content,
                )

            # Update Root name path
            norm_new = new_filepath.replace("\\", "/")
            content = re.sub(r'(\bname\s+")([^"]+)(")', f'\\1{norm_new}\\3', content, count=1)

            with open(new_filepath, "w", encoding="utf-8") as f:
                f.write(content)

            return True, f"Versioned up Nuke script to {os.path.basename(new_filepath)} (Write paths updated to {new_ver})"
        except Exception as e:
            return False, f"Failed to version up Nuke script: {e}"

    def _generate_bootstrapped_nk(
        self,
        scene_filepath: str,
        shot_context: Dict[str, Any],
        include_nodes: bool = True,
    ) -> str:
        from core.timecode import format_shot_id

        seq = shot_context.get("seq", "R01")
        shot = shot_context.get("shot", "0010")
        shot_id = shot_context.get("shot_id") or format_shot_id(seq, shot)
        version = shot_context.get("version", "v0001")
        m_vnum = re.search(r"(\d+)", version or "")
        version_num = int(m_vnum.group(1)) if m_vnum else 1
        start_f = int(shot_context.get("start_frame", 1001))
        end_f = int(shot_context.get("end_frame", 1065))
        fps = float(shot_context.get("fps", 24.0))
        width = int(shot_context.get("width", 1920))
        height = int(shot_context.get("height", 1080))
        vfx_root = (shot_context.get("vfx_root") or shot_context.get("prism_root") or "").replace("\\", "/").rstrip("/")
        norm_scene = scene_filepath.replace("\\", "/")

        format_name = f"VFX_{width}x{height}" if (width, height) != (1920, 1080) else "HD_1080"
        format_str = f"{width} {height} 0 0 {width} {height} 1 {format_name}"

        lines = [
            "#! Nuke Scene Auto-Bootstrapped by VFX Pipeline Hub",
            "Root {",
            " inputs 0",
            f' name "{norm_scene}"',
            f" frame {start_f}",
            f" first_frame {start_f}",
            f" last_frame {end_f}",
            " lock_range true",
            f" fps {fps:g}",
            f' format "{format_str}"',
            " colorManagement OCIO",
            " OCIO_config custom",
            " workingSpaceLUT scene_linear",
            "}",
        ]

        if not include_nodes:
            return "\n".join(lines) + "\n"

        plates: List[Dict[str, Any]] = shot_context.get("plates", [])
        # Build Read nodes for each ingested plate (or default placeholder if none yet)
        if not plates and vfx_root:
            default_plate_file = (
                f"{vfx_root}/{seq}/{shot}/media/plate/v0001/"
                f"{shot_id}_plate_v0001.%04d.exr"
            )
            plates = [{
                "identifier": "plate",
                "version": "v0001",
                "nuke_pattern": default_plate_file,
                "start_frame": start_f,
                "end_frame": end_f,
                "width": width,
                "height": height,
            }]

        # Backdrop for Plates
        bd_width = max(280, len(plates) * 200 + 80)
        lines.extend([
            "BackdropNode {",
            " inputs 0",
            " name Backdrop_Plates",
            " tile_color 0x263238ff",
            f' label "<center><b>{shot_id} INGESTED PLATES</b></center>"',
            " note_font_size 18",
            " xpos -60",
            " ypos -260",
            f" bdwidth {bd_width}",
            " bdheight 200",
            "}",
        ])

        primary_read_name = ""
        for idx, p in enumerate(plates):
            ident = p.get("identifier", f"plate_{idx+1}")
            ver = p.get("version", "v0001")
            file_pat = p.get("nuke_pattern", "").replace("\\", "/")
            p_start = int(p.get("start_frame", start_f))
            p_end = int(p.get("end_frame", end_f))
            node_name = re.sub(r"[^a-zA-Z0-9_]", "_", f"Read_{ident}_{ver}")
            xpos = idx * 180
            ypos = -160

            if idx == 0 or ident.lower() == "plate":
                primary_read_name = node_name

            lines.extend([
                "Read {",
                " inputs 0",
                f' file "{file_pat}"',
                f' format "{format_str}"',
                f" first {p_start}",
                f" last {p_end}",
                f" origfirst {p_start}",
                f" origlast {p_end}",
                " origset true",
                f" name {node_name}",
                f' label "{ident} ({ver})\\n[value first]-[value last]"',
                f" xpos {xpos}",
                f" ypos {ypos}",
                "}",
            ])

        # Add a Dot and pre-configured Pipeline Write node for comp output
        comp_render_dir = f"{vfx_root}/{seq}/{shot}/media/comp/{version}" if vfx_root else f"media/comp/{version}"
        comp_render_file = f"{comp_render_dir}/{shot_id}_comp_{version}.%04d.exr"

        if primary_read_name:
            lines.extend([
                f"push ${primary_read_name}",
            ])

        lines.extend([
            "Dot {",
            " name Dot_Comp_Pipe",
            ' label " COMP PIPE"',
            " note_font_size 14",
            " xpos 34",
            " ypos 40",
            "}",
            "Write {",
            f' file "{comp_render_file}"',
            " channels rgba",
            " file_type exr",
            ' datatype "16 bit half"',
            ' compression "Zip (1 scanline)"',
            " create_directories true",
            " first_part rgba",
            f" first {start_f}",
            f" last {end_f}",
            " use_limit true",
            " name Write_Comp",
            ' label "[value vfx_identifier] ([value vfx_resolved_version])\\n[file tail [value file]]"',
            " xpos 0",
            " ypos 160",
            " addUserKnob {20 pipeline_tab l Pipeline}",
            " addUserKnob {6 vfx_pipeline_write +INVISIBLE}",
            " vfx_pipeline_write true",
            ' addUserKnob {1 vfx_shot_id l "Shot ID"}',
            f' vfx_shot_id "{shot_id}"',
            ' addUserKnob {1 vfx_identifier l Identifier}',
            ' vfx_identifier "comp"',
            ' addUserKnob {4 vfx_version_mode l "Version Mode" M {"Auto Next" "Match Script" "Manual" ""}}',
            ' vfx_version_mode "Match Script"',
            ' addUserKnob {3 vfx_version_num l "Manual Version" +DISABLED}',
            f" vfx_version_num {version_num}",
            ' addUserKnob {1 vfx_resolved_version l "Resolved Version"}',
            f' vfx_resolved_version "{version}"',
            ' addUserKnob {26 vfx_div1 l ""}',
            ' addUserKnob {22 vfx_refresh_btn l "Refresh Path" T "from dccs.nuke.writer import refresh_write_node; refresh_write_node(nuke.thisNode())" +STARTLINE}',
            ' addUserKnob {22 vfx_next_ver_btn l "Next Version (+1)" T "from dccs.nuke.writer import bump_write_next_version; bump_write_next_version(nuke.thisNode())"}',
            ' addUserKnob {22 vfx_open_dir_btn l "Open Folder" T "from dccs.nuke.writer import open_write_folder; open_write_folder(nuke.thisNode())"}',
            ' addUserKnob {22 vfx_create_read_btn l "Create Read" T "from dccs.nuke.writer import create_read_from_write; create_read_from_write(nuke.thisNode())"}',
            ' addUserKnob {26 vfx_div2 l ""}',
            ' addUserKnob {6 vfx_auto_thumb l "Update Project Manager Thumbnail on Render" +STARTLINE}',
            " vfx_auto_thumb true",
            "}",
            "Viewer {",
            " frame_range_lock true",
            f' frame_range "{start_f}-{end_f}"',
            f" fps {fps:g}",
            " name Viewer1",
            " xpos 0",
            " ypos 280",
            "}",
        ])

        return "\n".join(lines) + "\n"
