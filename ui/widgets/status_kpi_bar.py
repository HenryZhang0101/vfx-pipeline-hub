"""Dynamic Notion-Dark Status KPI Bar with colored filter pills and a segmented color progress bar."""

from typing import Dict, List, Optional, Tuple

from qtpy.QtCore import QRectF, Qt, Signal
from qtpy.QtGui import QColor, QPainter, QPainterPath
from qtpy.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from core.models import DEFAULT_OPTION_COLOR, Project, Shot, default_status_property
from ui.widgets.property_widgets import make_color_dot_icon, normalize_qcolor


class SegmentedProgressBar(QWidget):
    """Thin proportional multi-color progress bar showing shot distribution across statuses."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._segments: List[Tuple[str, str, int]] = []
        self._total: int = 0
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_segments(self, segments: List[Tuple[str, str, int]], total: int) -> None:
        self._segments = [(name, col, count) for name, col, count in (segments or []) if count > 0]
        self._total = max(0, int(total))
        if self._total > 0 and self._segments:
            parts = [
                f"{name}: {count} ({round(count * 100 / self._total)}%)"
                for name, _, count in self._segments
            ]
            self.setToolTip("  ·  ".join(parts))
        else:
            self.setToolTip("No shots yet")
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        if w <= 0 or h <= 0:
            p.end()
            return

        radius = h / 2.0
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0.0, 0.0, w, h), radius, radius)
        p.setClipPath(clip_path)

        # Base track
        p.fillRect(QRectF(0.0, 0.0, w, h), QColor("#2A2A2A"))

        if self._total <= 0 or not self._segments:
            p.end()
            return

        gap = 1.5 if len(self._segments) > 1 else 0.0
        usable_w = max(0.0, w - gap * (len(self._segments) - 1))
        x = 0.0
        for idx, (_, col_hex, count) in enumerate(self._segments):
            if idx == len(self._segments) - 1:
                seg_w = max(0.0, w - x)
            else:
                seg_w = usable_w * (count / float(self._total))
            if seg_w > 0.0:
                p.fillRect(QRectF(x, 0.0, seg_w, h), normalize_qcolor(col_hex))
                x += seg_w + gap
        p.end()


class StatusKPIBar(QFrame):
    """
    Notion-Dark interactive KPI summary bar driven by Project.get_status_property().options:
      - Total pill + one colored clickable pill per configured status (0-count statuses dimmed)
      - Segmented color progress bar along the bottom edge
      - Click-to-filter synced bidirectionally with ShotExplorerPanel
    """

    status_filter_selected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("kpi_bar_frame")
        self._active_filter: str = ""
        self._total: int = 0
        self._status_rows: List[Tuple[str, str, int]] = []  # (name, color_hex, count)
        self._pill_buttons: Dict[str, QPushButton] = {}
        self._total_btn: Optional[QPushButton] = None

        self.setStyleSheet(
            "QFrame#kpi_bar_frame { background-color: #202020; border: 1px solid #2F2F2F; "
            "border-radius: 6px; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(5)

        self._pills_host = QWidget(self)
        self._pills_host.setStyleSheet("background: transparent;")
        self._pills_layout = QHBoxLayout(self._pills_host)
        self._pills_layout.setContentsMargins(0, 0, 0, 0)
        self._pills_layout.setSpacing(5)
        outer.addWidget(self._pills_host)

        self.progress_bar = SegmentedProgressBar(self)
        outer.addWidget(self.progress_bar)

        # Render empty default state before a project is loaded
        self.update_data(None, [], active_filter="")

    def update_data(
        self,
        project: Optional[Project],
        shots: List[Shot],
        active_filter: str = "",
    ) -> None:
        """Recompute counts from the project's Status property options and rebuild pills + bar."""
        self._active_filter = (active_filter or "").strip()
        if self._active_filter.lower() == "all statuses":
            self._active_filter = ""

        status_prop = project.get_status_property() if project else default_status_property()
        options = list(status_prop.options) if status_prop and status_prop.options else default_status_property().options

        counts_by_low: Dict[str, int] = {o.name.lower(): 0 for o in options}
        other_count = 0
        for s in shots or []:
            st_low = (s.status or "Pending").strip().lower()
            if st_low in counts_by_low:
                counts_by_low[st_low] += 1
            else:
                other_count += 1

        rows: List[Tuple[str, str, int]] = [
            (opt.name, opt.color, counts_by_low.get(opt.name.lower(), 0))
            for opt in options
        ]
        if other_count > 0:
            rows.append(("Other", DEFAULT_OPTION_COLOR, other_count))

        self._total = len(shots or [])
        self._status_rows = rows
        self._rebuild_pills()
        self.progress_bar.set_segments(rows, self._total)

    def set_active_filter(self, status_name: str) -> None:
        """Update active pill highlight without emitting status_filter_selected."""
        norm = (status_name or "").strip()
        if norm.lower() == "all statuses":
            norm = ""
        if norm == self._active_filter:
            return
        self._active_filter = norm
        self._refresh_pill_styles()

    def _clear_pills(self) -> None:
        while self._pills_layout.count():
            item = self._pills_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._pill_buttons.clear()
        self._total_btn = None

    def _rebuild_pills(self) -> None:
        self._clear_pills()

        # Total pill (resets filter to All Statuses)
        total_btn = QPushButton(f"Total  {self._total}")
        total_btn.setCursor(Qt.PointingHandCursor)
        total_btn.setToolTip("Show all shots (clear status filter)")
        total_btn.clicked.connect(lambda: self._on_pill_clicked(""))
        self._pills_layout.addWidget(total_btn)
        self._total_btn = total_btn

        # Subtle vertical divider
        div = QFrame()
        div.setFixedWidth(1)
        div.setFixedHeight(16)
        div.setStyleSheet("background-color: #2F2F2F; border: none;")
        self._pills_layout.addWidget(div)

        # Dynamic status pills
        for name, col_hex, count in self._status_rows:
            btn = QPushButton(f"{name}  {count}")
            btn.setCursor(Qt.PointingHandCursor)
            dot_color = col_hex if count > 0 else "#4A4A48"
            btn.setIcon(make_color_dot_icon(dot_color, 8))
            pct = f" ({round(count * 100 / self._total)}%)" if self._total > 0 else ""
            if name == "Other":
                btn.setToolTip(f"Unlisted status: {count} shot(s){pct}")
                btn.setEnabled(False)
            else:
                btn.setToolTip(f"Filter by {name}: {count} shot(s){pct} (click again to clear)")
                btn.clicked.connect(lambda _=False, st=name: self._on_pill_clicked(st))
            self._pills_layout.addWidget(btn)
            self._pill_buttons[name] = btn

        self._refresh_pill_styles()

    def _refresh_pill_styles(self) -> None:
        # Total pill styling
        if self._total_btn is not None:
            is_all = not self._active_filter
            if is_all:
                self._total_btn.setStyleSheet(
                    "QPushButton { background-color: #2B2B2B; color: #E3E2E0; "
                    "border: 1px solid #444444; border-radius: 4px; padding: 2px 8px; "
                    "font-size: 11px; font-weight: 700; }"
                    "QPushButton:hover { background-color: #333333; }"
                )
            else:
                self._total_btn.setStyleSheet(
                    "QPushButton { background-color: transparent; color: #9B9A97; "
                    "border: 1px solid transparent; border-radius: 4px; padding: 2px 8px; "
                    "font-size: 11px; font-weight: 600; }"
                    "QPushButton:hover { background-color: #2A2A2A; color: #E3E2E0; }"
                )

        # Status pills styling
        active_low = self._active_filter.lower()
        for name, col_hex, count in self._status_rows:
            btn = self._pill_buttons.get(name)
            if btn is None:
                continue
            c = normalize_qcolor(col_hex)
            is_active = bool(active_low) and (name.lower() == active_low)
            if is_active:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.36); "
                    f"color: #FFFFFF; border: 1px solid {c.lighter(120).name()}; "
                    f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}"
                    f"QPushButton:hover {{ background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.46); }}"
                )
            elif count > 0:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.16); "
                    f"color: #E3E2E0; border: 1px solid rgba({c.red()}, {c.green()}, {c.blue()}, 0.34); "
                    f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600; }}"
                    f"QPushButton:hover {{ background-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.28); "
                    f"border-color: rgba({c.red()}, {c.green()}, {c.blue()}, 0.60); }}"
                )
            else:
                # Zero-count dimmed pill (preserves layout stability)
                btn.setStyleSheet(
                    "QPushButton { background-color: transparent; color: #6B6A67; "
                    "border: 1px solid transparent; border-radius: 4px; padding: 2px 8px; "
                    "font-size: 11px; font-weight: 500; }"
                    "QPushButton:hover { background-color: #282828; color: #9B9A97; border-color: #333333; }"
                )

    def _on_pill_clicked(self, status_name: str) -> None:
        if not status_name:
            target = ""
        elif self._active_filter.lower() == status_name.lower():
            target = ""
        else:
            target = status_name
        self._active_filter = target
        self._refresh_pill_styles()
        self.status_filter_selected.emit(target)
