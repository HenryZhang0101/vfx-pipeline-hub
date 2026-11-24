"""
Notion Dark minimalist theme & user-configurable accent color for VFX Pipeline Hub.
"""

import os
import sys

try:
    from qtpy.QtGui import QColor, QIcon
except ImportError:
    from PySide6.QtGui import QColor, QIcon
from config import APP_ICON_ICO, APP_ICON_PNG, PIPELINE_ICONS_DIR, load_user_settings

DEFAULT_ACCENT_COLOR = "#2383E2"

ACCENT_PRESETS = [
    ("Notion Blue", "#2383E2"),
    ("Sage", "#4DAB9A"),
    ("Amber", "#FFA344"),
    ("Violet", "#9065B0"),
    ("Rose", "#E255A1"),
    ("Neutral", "#525252"),
]

NOTION_DARK_TOKENS = {
    "bg": "#191919",
    "sidebar": "#202020",
    "elevated": "#262626",
    "hover": "#2C2C2C",
    "border": "#2F2F2F",
    "border_strong": "#3E3E3E",
    "text": "#E3E2E0",
    "muted": "#9B9A97",
    "disabled_text": "#5A5A58",
    "success": "#2E7D5B",
    "success_hover": "#37946C",
    "warn": "#C24E2D",
    "warn_hover": "#D65A37",
}


def _normalize_hex(color_hex: str, fallback: str = DEFAULT_ACCENT_COLOR) -> str:
    c = QColor(color_hex)
    return c.name() if c.isValid() else fallback


def _adjust_hex(color_hex: str, factor: int = 115) -> str:
    c = QColor(_normalize_hex(color_hex))
    return c.lighter(factor).name()


def _rgba_from_hex(color_hex: str, alpha: float) -> str:
    c = QColor(_normalize_hex(color_hex))
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


def build_notion_dark_qss(accent_color: str = DEFAULT_ACCENT_COLOR) -> str:
    """Build the Notion Dark QSS stylesheet with the given hex accent color."""
    t = NOTION_DARK_TOKENS
    accent = _normalize_hex(accent_color)
    accent_hover = _adjust_hex(accent, 114)
    sel_bg = _rgba_from_hex(accent, 0.24)
    sel_bg_inactive = _rgba_from_hex(accent, 0.14)
    arrow_url = os.path.join(PIPELINE_ICONS_DIR, "dropdown_arrow.png").replace("\\", "/")
    arrow_hover_url = os.path.join(PIPELINE_ICONS_DIR, "dropdown_arrow_hover.png").replace("\\", "/")
    return f"""
QMainWindow, QDialog, QWidget {{
    background-color: {t["bg"]};
    color: {t["text"]};
    font-family: "Segoe UI", "Inter", -apple-system, sans-serif;
    font-size: 12px;
}}
QCheckBox, QLabel {{
    background-color: transparent;
}}
QGroupBox {{
    background-color: {t["sidebar"]};
    border: 1px solid {t["border"]};
    border-radius: 6px;
    margin-top: 10px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: {t["text"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {t["muted"]};
}}
QFrame#card_frame {{
    background-color: {t["sidebar"]};
    border: 1px solid {t["border"]};
    border-radius: 6px;
    padding: 8px;
}}
QFrame#header_bar {{
    background-color: {t["bg"]};
    border-bottom: 1px solid {t["border"]};
    padding: 6px 10px;
}}
QPushButton {{
    background-color: {t["elevated"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: 5px;
    padding: 5px 12px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t["hover"]};
    border-color: {t["border_strong"]};
}}
QPushButton:pressed {{
    background-color: {t["sidebar"]};
}}
QPushButton:disabled {{
    background-color: {t["sidebar"]};
    color: {t["disabled_text"]};
    border-color: {t["border"]};
}}
QPushButton#primary_btn {{
    background-color: {accent};
    color: #ffffff;
    border: 1px solid {accent};
    font-weight: 600;
}}
QPushButton#primary_btn:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#success_btn {{
    background-color: {t["success"]};
    color: #ffffff;
    border: 1px solid {t["success"]};
    font-weight: 600;
}}
QPushButton#success_btn:hover {{
    background-color: {t["success_hover"]};
}}
QPushButton#accent_btn {{
    background-color: {t["elevated"]};
    color: {accent_hover};
    border: 1px solid {accent};
    font-weight: 600;
}}
QPushButton#accent_btn:hover {{
    background-color: {t["hover"]};
}}
QPushButton#warn_btn {{
    background-color: {t["warn"]};
    color: #ffffff;
    border: 1px solid {t["warn"]};
    font-weight: 600;
}}
QPushButton#warn_btn:hover {{
    background-color: {t["warn_hover"]};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {t["sidebar"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: 5px;
    padding: 4px 8px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {accent};
}}
QComboBox {{
    background-color: {t["sidebar"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: 5px;
    padding: 4px 24px 4px 10px;
    font-weight: 500;
    combobox-popup: 0;
}}
QComboBox:hover, QComboBox:on {{
    background-color: {t["hover"]};
    border-color: {t["border_strong"]};
}}
QComboBox:focus {{
    border: 1px solid {accent};
}}
QComboBox:disabled {{
    background-color: {t["sidebar"]};
    color: {t["disabled_text"]};
    border-color: {t["border"]};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{arrow_url}");
    width: 10px;
    height: 10px;
    margin-right: 4px;
}}
QComboBox::down-arrow:on {{
    image: url("{arrow_hover_url}");
}}
QComboBox QAbstractItemView {{
    background-color: {t["sidebar"]};
    color: {t["text"]};
    border: 1px solid {t["border_strong"]};
    border-radius: 6px;
    padding: 4px;
    outline: 0;
    selection-background-color: {t["hover"]};
    selection-color: {t["text"]};
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 12px;
    border-radius: 4px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover,
QComboBox QAbstractItemView::item:selected {{
    background-color: {t["hover"]};
    color: {t["text"]};
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QTableView, QTableWidget {{
    background-color: {t["bg"]};
    alternate-background-color: {t["sidebar"]};
    color: {t["text"]};
    border: 1px solid {t["border"]};
    border-radius: 6px;
    gridline-color: {t["border"]};
    selection-background-color: {sel_bg};
    selection-color: #ffffff;
    outline: 0;
}}
QTableView::item, QTableWidget::item {{
    padding: 4px 8px;
    border: none;
    outline: none;
}}
QTableView::item:hover, QTableWidget::item:hover {{
    background-color: {t["hover"]};
    color: {t["text"]};
}}
QTableView::item:selected, QTableWidget::item:selected {{
    background-color: {sel_bg};
    color: #ffffff;
}}
QTableView::item:selected:!active, QTableWidget::item:selected:!active {{
    background-color: {sel_bg_inactive};
    color: {t["text"]};
}}
QTableView QComboBox, QTableWidget QComboBox {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px 6px;
}}
QTableView QComboBox:hover, QTableView QComboBox:focus,
QTableWidget QComboBox:hover, QTableWidget QComboBox:focus {{
    background-color: {t["elevated"]};
    border: 1px solid {t["border_strong"]};
}}
QHeaderView::section {{
    background-color: {t["sidebar"]};
    color: {t["muted"]};
    font-weight: 600;
    padding: 6px;
    border: none;
    border-right: 1px solid {t["border"]};
    border-bottom: 1px solid {t["border"]};
}}
QMenuBar {{
    background-color: {t["bg"]};
    color: {t["text"]};
    border-bottom: 1px solid {t["border"]};
    padding: 2px 4px;
    spacing: 4px;
}}
QMenuBar::item {{
    background-color: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    background-color: {t["hover"]};
    color: {t["text"]};
}}
QMenu {{
    background-color: {t["sidebar"]};
    color: {t["text"]};
    border: 1px solid {t["border_strong"]};
    border-radius: 6px;
    padding: 4px;
}}
QMenuBar QMenu {{
    min-width: 260px;
}}
QMenu::item {{
    padding: 5px 36px 5px 12px;
    border-radius: 4px;
    margin: 1px 0px;
}}
QMenu::item:selected {{
    background-color: {t["hover"]};
    color: {t["text"]};
}}
QMenu::item:disabled {{
    color: {t["disabled_text"]};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t["border"]};
    margin: 4px 6px;
}}
QSplitter::handle {{
    background-color: {t["border"]};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QStatusBar {{
    background-color: {t["bg"]};
    color: {t["muted"]};
    border-top: 1px solid {t["border"]};
}}
QListWidget#settings_sidebar {{
    background-color: {t["sidebar"]};
    border: none;
    border-right: 1px solid {t["border"]};
    outline: 0;
    padding: 8px 6px;
}}
QListWidget#settings_sidebar::item {{
    color: {t["muted"]};
    padding: 7px 10px;
    margin: 1px 0px;
    border-radius: 5px;
    border: none;
}}
QListWidget#settings_sidebar::item:hover {{
    background-color: {t["hover"]};
    color: {t["text"]};
}}
QListWidget#settings_sidebar::item:selected {{
    background-color: {sel_bg};
    color: {t["text"]};
    font-weight: 600;
}}
QFrame#settings_footer {{
    background-color: {t["sidebar"]};
    border-top: 1px solid {t["border"]};
}}
QLabel#settings_page_title {{
    color: {t["text"]};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#settings_page_subtitle {{
    color: {t["muted"]};
    font-size: 11px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {t["border_strong"]};
    border-radius: 3px;
    background-color: {t["sidebar"]};
}}
QCheckBox::indicator:hover {{
    border-color: {accent};
}}
QCheckBox::indicator:checked {{
    background-color: {accent};
    border-color: {accent};
}}
QCalendarWidget QWidget {{
    alternate-background-color: {t["sidebar"]};
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {t["elevated"]};
    border-bottom: 1px solid {t["border"]};
    padding: 4px;
}}
QCalendarWidget QToolButton {{
    color: {t["text"]};
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    font-weight: 600;
}}
QCalendarWidget QToolButton:hover {{
    background-color: {t["hover"]};
}}
QCalendarWidget QAbstractItemView:enabled {{
    background-color: {t["bg"]};
    color: {t["text"]};
    selection-background-color: {accent};
    selection-color: #ffffff;
}}
QCalendarWidget QAbstractItemView:disabled {{
    color: {t["disabled_text"]};
}}
"""


def get_active_stylesheet() -> str:
    """Return the Notion Dark stylesheet using the user's saved accent_color."""
    settings = load_user_settings()
    return build_notion_dark_qss(settings.get("accent_color", DEFAULT_ACCENT_COLOR))


def get_app_icon() -> QIcon:
    """Return the shared VFX Pipeline Hub application icon (multi-size ICO on Windows, PNG elsewhere)."""
    candidates = [APP_ICON_ICO, APP_ICON_PNG] if sys.platform == "win32" else [APP_ICON_PNG, APP_ICON_ICO]
    for path in candidates:
        if path and os.path.isfile(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return QIcon()
