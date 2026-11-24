import csv
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import DEFAULT_HANDLES
from core.models import Project, Shot, ShotProperty
from core.timecode import get_png_dimensions


def load_shots_from_csv(file_path: str) -> List[Shot]:
    if not file_path or not os.path.exists(file_path):
        return []
    shots: List[Shot] = []
    with open(file_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            shots.append(Shot.from_list(row))
    return shots


def load_shots_from_excel(file_path: str) -> List[Shot]:
    """Loads Shot list from an Excel (.xlsx) file."""
    if not file_path or not os.path.exists(file_path):
        return []
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        header_row_idx = 0
        for idx, r in enumerate(rows):
            r_lower = [str(c or "").strip().lower() for c in r if c is not None]
            if "shot id" in r_lower:
                header_row_idx = idx
                break

        headers = [str(h or "").strip() for h in rows[header_row_idx]]
        col_idx = {name.lower(): idx for idx, name in enumerate(headers) if name}

        def get_cell(r, col_name: str, default: str = "") -> str:
            idx = col_idx.get(col_name.lower())
            if idx is None or idx >= len(r) or r[idx] is None:
                return default
            return str(r[idx]).strip()

        intrinsic_lower = {h.lower() for h in Shot.EXCEL_HEADERS}
        extra_headers = [h for h in headers if h and h.lower() not in intrinsic_lower]

        shots: List[Shot] = []
        for r in rows[header_row_idx + 1:]:
            if not r or not any(cell is not None and str(cell).strip() != "" for cell in r):
                continue
            sid = get_cell(r, "Shot ID")
            cname = get_cell(r, "Clip Name")
            if not sid and not cname:
                continue
            custom_fields = {}
            for eh in extra_headers:
                val = get_cell(r, eh)
                if val != "":
                    custom_fields[eh] = val
            shots.append(
                Shot(
                    shot_id=sid,
                    name=cname,
                    thumb=get_cell(r, "Thumbnails"),
                    dur=get_cell(r, "Duration"),
                    start=get_cell(r, "Start TC"),
                    end=get_cell(r, "End TC"),
                    status=get_cell(r, "Status", "Pending"),
                    assign=get_cell(r, "Assign"),
                    notes=get_cell(r, "Notes"),
                    handles=get_cell(r, "Handles", str(DEFAULT_HANDLES)),
                    custom_fields=custom_fields,
                )
            )
        wb.close()
        return shots
    except Exception as e:
        print(f"Error loading shots from Excel '{file_path}': {e}")
        return []


def merge_shots_from_excel(project: Project, file_path: str) -> Tuple[int, int, int]:
    """
    Loads shots from an Excel (.xlsx) file and merges them into `project` by Shot ID.
    - Matching shots: updates Excel-backed fields (name, duration, handles, start_tc,
      end_tc, status, assign, notes, custom_fields) while preserving internal metadata
      (thumb, fps, resolution, start_frame, comp_version).
    - New shots: added to `project`.
    - Unlisted shots in `project`: preserved untouched.
    Returns (total_loaded, updated_count, added_count).
    """
    loaded = load_shots_from_excel(file_path)
    if not loaded:
        return 0, 0, 0

    existing_prop_names = {p.name.strip().lower() for p in project.shot_properties if p.name}
    updated_count = 0
    added_count = 0

    for incoming in loaded:
        if not incoming.shot_id:
            continue
        for cf_name in incoming.custom_fields.keys():
            cf_clean = str(cf_name).strip()
            if cf_clean and cf_clean.lower() not in existing_prop_names:
                project.shot_properties.append(ShotProperty(key="", name=cf_clean, prop_type="text"))
                existing_prop_names.add(cf_clean.lower())

        existing = project.get_shot(incoming.shot_id)
        if existing is not None:
            if incoming.name:
                existing.name = incoming.name
            if incoming.duration:
                existing.duration = incoming.duration
            if incoming.handles:
                existing.handles = incoming.handles
            if incoming.start_tc:
                existing.start_tc = incoming.start_tc
            if incoming.end_tc:
                existing.end_tc = incoming.end_tc
            if incoming.status:
                existing.status = incoming.status
            existing.assign = incoming.assign
            existing.notes = incoming.notes
            for cf_k, cf_v in incoming.custom_fields.items():
                existing.custom_fields[cf_k] = cf_v
            updated_count += 1
        else:
            project.add_shot(incoming)
            added_count += 1

    return len(loaded), updated_count, added_count


def save_shots_to_excel(
    file_path: str,
    shots: List[Shot],
    thumb_dir: Optional[str] = None,
    project_name: str = "",
    last_modified: Optional[str] = None,
    custom_field_names: Optional[List[str]] = None,
    status_colors: Optional[Dict[str, str]] = None,
) -> bool:
    """
    Creates or updates an Excel (.xlsx) shot breakdown with KPI summary rows (1-3)
    and the master shot table below (Row 5+), embedding PNG thumbnails without Pillow.
    """
    if not file_path:
        return False
    try:
        import openpyxl
        from openpyxl.drawing.image import Image as OpenpyxlImage
        from openpyxl.styles import Alignment, Font, PatternFill

        class DirectPNGExcelImage(OpenpyxlImage):
            def __init__(self, png_path: str, display_w: int = 120):
                self.ref = png_path
                orig_w, orig_h = get_png_dimensions(png_path)
                scale = display_w / float(max(1, orig_w))
                self.width = display_w
                self.height = int(round(orig_h * scale))
                self.format = "png"

            def _data(self):
                with open(self.ref, "rb") as f:
                    return f.read()

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "VFX Shots"

        proj_display = project_name or os.path.basename(os.path.dirname(os.path.dirname(file_path)))
        if not last_modified:
            last_modified = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        count_total = len(shots)
        count_approved = sum(1 for s in shots if "appr" in (s.status or "").lower())
        count_wip = sum(1 for s in shots if "wip" in (s.status or "").lower())
        count_review = sum(1 for s in shots if "rev" in (s.status or "").lower())
        count_omit = sum(1 for s in shots if "omit" in (s.status or "").lower())
        count_pending = max(0, count_total - (count_approved + count_wip + count_review + count_omit))

        if custom_field_names is not None:
            extra_cols: List[str] = [str(c).strip() for c in custom_field_names if str(c).strip()]
        else:
            extra_cols = []
            for s in shots:
                for k in s.custom_fields.keys():
                    if k not in extra_cols:
                        extra_cols.append(k)

        headers = list(Shot.EXCEL_HEADERS) + extra_cols
        last_col_letter = openpyxl.utils.get_column_letter(len(headers))
        ws.merge_cells(f"A1:{last_col_letter}1")
        title_cell = ws.cell(row=1, column=1, value=f"VFX SHOT BREAKDOWN & DASHBOARD - {proj_display.upper()}")
        title_cell.font = Font(name="Segoe UI", size=13, bold=True, color="FFFFFF")
        title_cell.fill = PatternFill(start_color="1E2227", end_color="1E2227", fill_type="solid")
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 30

        kpi_labels = ["Project", "Last Modified", "Total Shots", "Approved", "In Progress (WIP)", "In Review", "Pending", "Omit"]
        ws.row_dimensions[2].height = 20
        for col_i, lbl in enumerate(kpi_labels, start=1):
            c = ws.cell(row=2, column=col_i, value=lbl)
            c.font = Font(name="Segoe UI", size=9, bold=True, color="ABB2BF")
            c.fill = PatternFill(start_color="2C313A", end_color="2C313A", fill_type="solid")
            c.alignment = Alignment(horizontal="center", vertical="center")

        kpi_values = [proj_display, last_modified, count_total, count_approved, count_wip, count_review, count_pending, count_omit]
        kpi_colors = [
            ("3B4048", "FFFFFF"),
            ("3B4048", "61AFEF"),
            ("21252B", "FFFFFF"),
            ("1E3A29", "98C379"),
            ("3D2F1D", "E5C07B"),
            ("1C3247", "61AFEF"),
            ("282C34", "ABB2BF"),
            ("3D1F24", "E06C75"),
        ]
        ws.row_dimensions[3].height = 24
        for col_i, (val, (bg, fg)) in enumerate(zip(kpi_values, kpi_colors), start=1):
            c = ws.cell(row=3, column=col_i, value=val)
            c.font = Font(name="Segoe UI", size=10, bold=True, color=fg)
            c.fill = PatternFill(start_color=bg, end_color=bg, fill_type="solid")
            c.alignment = Alignment(horizontal="center", vertical="center")

        ws.row_dimensions[4].height = 10

        # Row 5: Master Table Headers (headers already built above)
        ws.row_dimensions[5].height = 24
        for col_num, h_name in enumerate(headers, start=1):
            c = ws.cell(row=5, column=col_num, value=h_name)
            c.font = Font(name="Segoe UI", size=10, bold=True, color="61AFEF")
            c.fill = PatternFill(start_color="282C34", end_color="282C34", fill_type="solid")
            c.alignment = Alignment(horizontal="left" if col_num != 3 else "center", vertical="center")

        ws.freeze_panes = "A6"

        for row_idx, shot in enumerate(shots, start=6):
            thumb_file = shot.thumb if (shot.thumb and os.path.isfile(shot.thumb)) else ""
            if not thumb_file and thumb_dir and shot.shot_id:
                candidate = os.path.join(thumb_dir, f"{shot.shot_id}.png")
                if os.path.isfile(candidate):
                    thumb_file = candidate
                    shot.thumb = candidate

            row_vals = [
                shot.shot_id,
                shot.name,
                "",
                shot.duration,
                shot.handles,
                shot.start_tc,
                shot.end_tc,
                shot.status,
                shot.assign,
                shot.notes,
            ] + [shot.custom_fields.get(col_name, "") for col_name in extra_cols]
            for col_idx, val in enumerate(row_vals, start=1):
                c = ws.cell(row=row_idx, column=col_idx, value=str(val))
                c.font = Font(name="Segoe UI", size=9)
                c.alignment = Alignment(vertical="center")

            st_lower = (shot.status or "").lower()
            st_cell = ws.cell(row=row_idx, column=8)
            custom_hex = ""
            if status_colors:
                for k, v in status_colors.items():
                    if k.strip().lower() == st_lower and v:
                        custom_hex = str(v).lstrip("#").strip()
                        break
            if len(custom_hex) == 6:
                try:
                    r_v = int(custom_hex[0:2], 16)
                    g_v = int(custom_hex[2:4], 16)
                    b_v = int(custom_hex[4:6], 16)
                    # Blend 25% toward white-pastel background for clean Excel readability
                    bg_hex = f"{int(255 * 0.78 + r_v * 0.22):02X}{int(255 * 0.78 + g_v * 0.22):02X}{int(255 * 0.78 + b_v * 0.22):02X}"
                    fg_hex = f"{int(r_v * 0.55):02X}{int(g_v * 0.55):02X}{int(b_v * 0.55):02X}"
                    st_cell.fill = PatternFill(start_color=bg_hex, end_color=bg_hex, fill_type="solid")
                    st_cell.font = Font(name="Segoe UI", size=9, bold=True, color=fg_hex)
                except ValueError:
                    custom_hex = ""
            if not custom_hex:
                if "appr" in st_lower:
                    st_cell.fill = PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid")
                    st_cell.font = Font(name="Segoe UI", size=9, bold=True, color="155724")
                elif "wip" in st_lower:
                    st_cell.fill = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
                    st_cell.font = Font(name="Segoe UI", size=9, bold=True, color="856404")
                elif "rev" in st_lower:
                    st_cell.fill = PatternFill(start_color="D1ECF1", end_color="D1ECF1", fill_type="solid")
                    st_cell.font = Font(name="Segoe UI", size=9, bold=True, color="0C5460")
                elif "omit" in st_lower:
                    st_cell.fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")
                    st_cell.font = Font(name="Segoe UI", size=9, bold=True, color="721C24")

            if thumb_file and os.path.isfile(thumb_file):
                ws.row_dimensions[row_idx].height = 54
                try:
                    img = DirectPNGExcelImage(thumb_file, display_w=120)
                    ws.add_image(img, f"C{row_idx}")
                except Exception as img_err:
                    print(f"Warning: Could not embed thumbnail for {shot.shot_id}: {img_err}")
            else:
                ws.row_dimensions[row_idx].height = 20

        for col_idx, h_name in enumerate(headers, start=1):
            col_letter = openpyxl.utils.get_column_letter(col_idx)
            if h_name == "Thumbnails":
                ws.column_dimensions[col_letter].width = 18
            else:
                max_len = max(len(h_name), 12)
                for r in range(6, len(shots) + 6):
                    val_str = str(ws.cell(row=r, column=col_idx).value or "")
                    max_len = max(max_len, len(val_str))
                ws.column_dimensions[col_letter].width = min(max_len + 3, 40)

        wb.save(file_path)
        return True
    except Exception as e:
        print(f"Error saving shots to Excel '{file_path}': {e}")
        return False
