import base64
import json
import os
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.models import Shot
from core.timecode import normalize_shot_id


def format_iso_timestamp(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    ts_str = str(ts_str).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            pass
    return None


def compare_timestamps(local_ts: str, remote_ts: str) -> str:
    dt_local = parse_timestamp(local_ts)
    dt_remote = parse_timestamp(remote_ts)

    if not dt_local and not dt_remote:
        return "IN_SYNC"
    if not dt_remote and dt_local:
        return "LOCAL_NEWER"
    if not dt_local and dt_remote:
        return "REMOTE_NEWER"

    diff_seconds = (dt_local - dt_remote).total_seconds()
    if abs(diff_seconds) <= 3:
        return "IN_SYNC"
    return "LOCAL_NEWER" if diff_seconds > 0 else "REMOTE_NEWER"


def get_status_counts(shots: List[Shot]) -> Dict[str, int]:
    counts = {
        "total": len(shots),
        "approved": 0,
        "wip": 0,
        "review": 0,
        "pending": 0,
        "omit": 0,
    }
    for s in shots:
        st = (s.status or "").strip().lower()
        if "appr" in st:
            counts["approved"] += 1
        elif "wip" in st:
            counts["wip"] += 1
        elif "rev" in st:
            counts["review"] += 1
        elif "omit" in st:
            counts["omit"] += 1
        else:
            counts["pending"] += 1
    return counts


class GoogleSheetSyncClient:
    """Client for communicating with the deployed Google Sheets Apps Script Webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = (webhook_url or "").strip()

    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.startswith("http"))

    def get_remote_meta(self) -> Tuple[bool, str, Dict[str, Any]]:
        if not self.is_configured():
            return False, "Google Sheet Webhook URL is not configured.", {}
        sep = "&" if "?" in self.webhook_url else "?"
        query_url = f"{self.webhook_url}{sep}action=get_meta"
        try:
            req = urllib.request.Request(
                query_url,
                headers={"User-Agent": "VFXPipelineHub/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("success"):
                    return True, "Metadata retrieved successfully.", payload
                return False, payload.get("error", "Error response from Google Sheet."), payload
        except Exception as e:
            return False, f"Connection error: {e}", {}

    def pull_all_shots(self) -> Tuple[bool, str, List[Dict[str, Any]], Dict[str, Any]]:
        if not self.is_configured():
            return False, "Google Sheet Webhook URL is not configured.", [], {}
        sep = "&" if "?" in self.webhook_url else "?"
        query_url = f"{self.webhook_url}{sep}action=get_all"
        try:
            req = urllib.request.Request(
                query_url,
                headers={"User-Agent": "VFXPipelineHub/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("success"):
                    shots_data = payload.get("shots", [])
                    meta = {
                        "last_modified": payload.get("last_modified", ""),
                        "project": payload.get("project", ""),
                        "summary": payload.get("summary", {}),
                    }
                    return True, f"Successfully pulled {len(shots_data)} shots from Google Sheet.", shots_data, meta
                return False, payload.get("error", "Failed to pull data from Google Sheet."), [], {}
        except Exception as e:
            return False, f"Failed to connect to Google Sheet: {e}", [], {}

    def push_shots(
        self,
        project_name: str,
        shots: List[Shot],
        last_modified: Optional[str] = None,
        thumb_dir: Optional[str] = None,
        custom_field_names: Optional[List[str]] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not self.is_configured():
            return False, "Google Sheet Webhook URL is not configured.", {}
        if not last_modified:
            last_modified = format_iso_timestamp()

        if custom_field_names is not None:
            extra_cols: List[str] = [str(c).strip() for c in custom_field_names if str(c).strip()]
        else:
            extra_cols = []
            for s in shots:
                for k in s.custom_fields.keys():
                    if k not in extra_cols:
                        extra_cols.append(k)

        summary = get_status_counts(shots)
        shot_dicts = []
        for s in shots:
            d = s.to_dict()
            for col_name in extra_cols:
                d[col_name] = s.custom_fields.get(col_name, "")
            t_file = s.thumb if (s.thumb and os.path.isfile(s.thumb)) else ""
            if not t_file and thumb_dir and s.shot_id:
                candidate = os.path.join(thumb_dir, f"{s.shot_id}.png")
                if os.path.isfile(candidate):
                    t_file = candidate
            if t_file and os.path.isfile(t_file):
                try:
                    with open(t_file, "rb") as f:
                        d["thumb_b64"] = base64.b64encode(f.read()).decode("utf-8")
                except Exception as e:
                    print(f"Warning: could not encode thumbnail for {s.shot_id}: {e}")
            shot_dicts.append(d)

        body = {
            "action": "push",
            "project_name": project_name,
            "last_modified": last_modified,
            "summary": summary,
            "headers": list(Shot.EXCEL_HEADERS) + extra_cols,
            "shots": shot_dicts,
        }
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "VFXPipelineHub/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("success"):
                    return True, f"Pushed {len(shots)} shots to Google Sheet successfully.", payload
                return False, payload.get("error", "Push failed on Google Sheet."), payload
        except Exception as e:
            return False, f"Failed to push to Google Sheet: {e}", {}


def smart_merge(local_shots: List[Shot], remote_shot_dicts: List[Dict[str, Any]]) -> List[Shot]:
    local_map = {normalize_shot_id(s.shot_id): s for s in local_shots}
    merged_list: List[Shot] = list(local_shots)

    for r_data in remote_shot_dicts:
        raw_sid = r_data.get("shot_id") or r_data.get("Shot ID") or ""
        sid = normalize_shot_id(raw_sid)
        if not sid:
            continue
        if sid in local_map:
            local_shot = local_map[sid]
            r_status = r_data.get("status") or r_data.get("Status")
            r_assign = r_data.get("assign") or r_data.get("Assign")
            r_notes = r_data.get("notes") or r_data.get("Notes")

            if r_status:
                local_shot.status = r_status
            if r_assign is not None:
                local_shot.assign = r_assign
            if r_notes is not None:
                local_shot.notes = r_notes
            r_cf = r_data.get("custom_fields")
            if isinstance(r_cf, dict):
                for k, v in r_cf.items():
                    local_shot.custom_fields[str(k)] = str(v)
        else:
            new_shot = Shot.from_dict(r_data)
            local_map[normalize_shot_id(new_shot.shot_id) or sid] = new_shot
            merged_list.append(new_shot)

    return merged_list
