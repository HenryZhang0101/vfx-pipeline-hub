import re
from typing import Dict, List, Tuple

from core.timecode import format_shot_id


def parse_edl_markers(file_path: str) -> Tuple[str, List[Dict[str, str]]]:
    """Parses an EDL file containing Resolve marker comments and returns (timeline_name, records)."""
    edl_records: List[Dict[str, str]] = []
    timeline_name = ""

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("TITLE:"):
            timeline_name = line[6:].strip()
            i += 1
            continue

        tokens = line.split()
        if len(tokens) >= 8 and tokens[0].isdigit() and tokens[2] in ("V", "A"):
            meta_line = ""
            j = i + 1
            while j < len(lines):
                if lines[j].strip():
                    meta_line = lines[j].strip()
                    break
                j += 1

            if meta_line:
                parts = [p.strip() for p in meta_line.split("|")]
                note = parts[0] if parts else ""
                raw_shot_id = ""
                duration_str = ""

                for part in parts[1:]:
                    if part.startswith("M:"):
                        raw_shot_id = part[2:].strip()
                    elif part.startswith("D:"):
                        duration_str = part[2:].strip()

                seq = ""
                shot = ""
                formatted_id = raw_shot_id
                if raw_shot_id:
                    if "-" in raw_shot_id or "_" in raw_shot_id:
                        sep = "-" if "-" in raw_shot_id else "_"
                        p = raw_shot_id.split(sep, 1)
                        seq = p[0]
                        m = re.match(r"^(\d+)(.*)$", p[1])
                        if m:
                            shot = f"{int(m.group(1)):04d}{m.group(2)}"
                        else:
                            shot = p[1]
                        formatted_id = format_shot_id(seq, shot)

                edl_records.append({
                    "seq": seq,
                    "shot": shot,
                    "shot_id": formatted_id,
                    "notes": note,
                    "start_tc": tokens[6],
                    "duration": duration_str,
                    "timeline": timeline_name,
                })
                i = j + 1
                continue
        i += 1

    return timeline_name, edl_records
