"""HAT look: warm grays from the logo, one typeface, lots of air."""
import os

from PySide6.QtGui import QFont, QIcon, QPixmap

ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

INK = "#4A4744"        # logo gray
INK_SOFT = "#7A7672"
LINE = "#DEDAD5"
PAPER = "#F7F6F4"
CARD = "#FFFFFF"
HOVER = "#EEEBE7"
ACCENT = "#4A4744"
WARN = "#B4693C"


def asset(name: str) -> str:
    return os.path.join(ASSETS, name)


def app_icon() -> QIcon:
    icon = QIcon()
    for n in ("hat_mark.png",):
        icon.addPixmap(QPixmap(asset(n)))
    return icon


def tray_icon(dark_taskbar: bool = True) -> QIcon:
    """Windows taskbars are dark by default, so the light mark reads better there."""
    return QIcon(asset("hat_mark_light.png" if dark_taskbar else "hat_mark.png"))


def pixmap(name: str, width: int = 0, height: int = 0, dpr: float = 1.0) -> QPixmap:
    """A crisp logo at any screen scale: scale to physical pixels, then mark the ratio."""
    pm = QPixmap(asset(name))
    from PySide6.QtCore import Qt
    if width:
        pm = pm.scaledToWidth(int(width * dpr), Qt.SmoothTransformation)
    elif height:
        pm = pm.scaledToHeight(int(height * dpr), Qt.SmoothTransformation)
    pm.setDevicePixelRatio(dpr)
    return pm


def base_font(size: float = 9.5) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"])
    f.setStyleHint(QFont.SansSerif)
    f.setPointSizeF(size)
    return f


STYLE = f"""
* {{ color: {INK}; }}
QWidget#Panel {{ background: {PAPER}; border: 1px solid {LINE}; border-radius: 12px; }}
QWidget#Window, QDialog, QMainWindow {{ background: {PAPER}; }}
QLabel#Height {{ font-size: 34pt; font-weight: 300; letter-spacing: 1px; }}
QLabel#Unit {{ font-size: 12pt; color: {INK_SOFT}; padding-bottom: 8px; }}
QLabel#Name {{ font-size: 10pt; font-weight: 600; }}
QLabel#Hint, QLabel#Status {{ color: {INK_SOFT}; font-size: 8.5pt; }}
QLabel#Section {{ font-size: 8pt; font-weight: 600; color: {INK_SOFT}; letter-spacing: 1px; }}
QFrame#Banner {{ background: #FBF1EA; border: 1px solid #EDCDB8; border-radius: 8px; }}
QFrame#Banner QLabel {{ color: {WARN}; }}

QPushButton {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 6px 12px; font-size: 9.5pt;
}}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ color: #B8B4AF; }}
QPushButton#Move {{
    background: {INK}; color: white; border: none; border-radius: 10px;
    font-size: 15pt; min-height: 46px;
}}
QPushButton#Move:hover {{ background: #5A5652; }}
QPushButton#Move:pressed {{ background: #2F2D2B; }}
QPushButton#Move:disabled {{ background: #B8B4AF; }}
QPushButton#Preset {{ min-height: 32px; font-weight: 600; }}
QPushButton#Preset[empty="true"] {{ color: #B8B4AF; font-weight: 400; }}
QPushButton#Icon {{ border: none; background: transparent; padding: 4px; font-size: 13pt; color: {INK_SOFT}; }}
QPushButton#Icon:hover {{ color: {INK}; background: {HOVER}; }}
QPushButton#Primary {{ background: {INK}; color: white; border: none; }}
QPushButton#Primary:hover {{ background: #5A5652; }}
QPushButton#Link {{ border: none; background: transparent; color: {WARN}; text-decoration: underline; padding: 0; }}

QProgressBar {{ background: {LINE}; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {INK}; border-radius: 2px; }}

QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 8px; background: {CARD}; top: -1px; }}
QTabBar::tab {{ padding: 7px 16px; color: {INK_SOFT}; border: none; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 2px solid {INK}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 6px; padding: 4px 6px; min-height: 20px;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus, QTimeEdit:focus {{ border-color: {INK_SOFT}; }}
QTableWidget, QListWidget {{
    background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; gridline-color: {HOVER};
    selection-background-color: {HOVER}; selection-color: {INK};
}}
QHeaderView::section {{ background: {CARD}; border: none; border-bottom: 1px solid {LINE};
    padding: 6px; font-weight: 600; color: {INK_SOFT}; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 8px; margin-top: 14px; padding: 10px; background: {CARD}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {INK_SOFT}; font-weight: 600; }}
QCheckBox {{ spacing: 6px; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 4px 6px; }}
QMenu {{ background: {CARD}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {HOVER}; }}
"""
