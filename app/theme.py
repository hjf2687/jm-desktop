"""主题：暗色/亮色两套配色 + 调色板 + QSS，支持运行时切换。"""
import os

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

from .settings import DATA_DIR

DARK = {
    'bg': '#15161a', 'panel': '#1a1c21', 'card': '#1c1e24', 'input': '#1d1f26',
    'list': '#17181d', 'popup': '#20222a', 'reader': '#101114',
    'border': '#262930', 'border2': '#333847',
    'text': '#e8e8ec', 'strong': '#f2f2f6', 'title': '#f5f5f8', 'card_text': '#dfe1e7',
    'muted': '#9aa0ad', 'faint': '#7d8491', 'hint': '#8a90a0', 'disabled': '#5d6270',
    'btn': '#262a34', 'btn_hover': '#2e333f', 'btn_pressed': '#22252e',
    'btn_disabled': '#20232b', 'btn_disabled_border': '#2a2d36',
    'accent': '#e8556d', 'accent_hover': '#ff6b82', 'white': '#ffffff',
    'accent_disabled': '#6b3a45', 'accent_disabled_text': '#c9b6ba',
    'ghost_border': '#3a3f4d', 'ghost_text': '#c6c9d2',
    'danger_bg': '#3a2229', 'danger_border': '#5d3a44', 'danger_text': '#ff8fa0', 'danger_hover': '#482831',
    'nav_text': '#b6bac4', 'nav_hover': '#242730', 'nav_checked_bg': '#2c2030', 'nav_checked_text': '#ffb3c0',
    'sel': '#38232b', 'scrollbar': '#3a3f4c', 'scrollbar_hover': '#4a5060',
    'progress_border': '#2b2e37',
}

LIGHT = {
    'bg': '#f2f3f7', 'panel': '#ffffff', 'card': '#ffffff', 'input': '#ffffff',
    'list': '#f7f8fb', 'popup': '#ffffff', 'reader': '#d8d9de',
    'border': '#d9dce3', 'border2': '#c3c7d1',
    'text': '#2b2d33', 'strong': '#1c1e24', 'title': '#16181d', 'card_text': '#3a3d45',
    'muted': '#6b7180', 'faint': '#8b91a0', 'hint': '#7a8090', 'disabled': '#a8adba',
    'btn': '#eef0f4', 'btn_hover': '#e2e5ec', 'btn_pressed': '#d6dae2',
    'btn_disabled': '#e9ebf0', 'btn_disabled_border': '#dcdfe6',
    'accent': '#e0485f', 'accent_hover': '#d13d53', 'white': '#ffffff',
    'accent_disabled': '#f0c3ca', 'accent_disabled_text': '#a0656e',
    'ghost_border': '#c3c7d1', 'ghost_text': '#4a4e58',
    'danger_bg': '#fbe8ec', 'danger_border': '#eec2ca', 'danger_text': '#c2405a', 'danger_hover': '#f7d9df',
    'nav_text': '#5a5f6b', 'nav_hover': '#e8eaf0', 'nav_checked_bg': '#fde8ec', 'nav_checked_text': '#c23a52',
    'sel': '#fadbe1', 'scrollbar': '#c3c7d1', 'scrollbar_hover': '#aeb3c0',
    'progress_border': '#dcdfe6',
}


# -- 数字框上下箭头 ----------------------------------------------------------
# Qt 的已知行为：一旦用 QSS 给 QSpinBox 设了边框/背景，::up-arrow / ::down-arrow
# 就再也不会走底层样式去画，必须显式给 image；也不支持 CSS 那种 border 拼三角形的写法。
# 所以这里按当前主题色现画一对小三角存成 PNG，供 QSS 引用（颜色变了就生成新文件）。
ARROW_DIR = os.path.join(DATA_DIR, '_icons')
ARROW_SCALE = 3          # 3 倍图，QSS 里按 9x6 显示，高分屏下不会糊
ARROW_W, ARROW_H = 9, 6


def _arrow_pixmap(color: str, up: bool) -> QPixmap:
    s = ARROW_SCALE
    w, h = ARROW_W * s, ARROW_H * s
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    m = 0.6 * s
    if up:
        pts = [QPointF(m, h - m), QPointF(w / 2, m), QPointF(w - m, h - m)]
    else:
        pts = [QPointF(m, m), QPointF(w - m, m), QPointF(w / 2, h - m)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    return pm


def _arrow_urls(c) -> dict:
    """确保当前主题配色的箭头图标存在，返回 {'up': url, 'down': url}（QSS 用正斜杠）。"""
    urls = {}
    try:
        os.makedirs(ARROW_DIR, exist_ok=True)
    except OSError:
        return {'up': '', 'down': ''}
    for name, color in (('up', c['muted']), ('down', c['muted'])):
        path = os.path.join(ARROW_DIR, 'spin_%s_%s_x%d.png'
                            % (name, color.lstrip('#'), ARROW_SCALE))
        if not os.path.exists(path):
            try:
                _arrow_pixmap(color, name == 'up').save(path, 'PNG')
            except OSError:
                return {'up': '', 'down': ''}
        urls[name] = path.replace('\\', '/')
    return urls


def _qss(c, arrows=None):
    arrows = arrows or {}
    up_img = f"image: url(\"{arrows['up']}\");" if arrows.get('up') else ''
    down_img = f"image: url(\"{arrows['down']}\");" if arrows.get('down') else ''
    return f'''
QWidget {{ background: transparent; color: {c['text']}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

#sidebar {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
#sideTitle {{ color: {c['strong']}; font-size: 17px; font-weight: 600; padding: 6px 10px; }}
#sideSub {{ color: {c['faint']}; font-size: 11px; padding: 0 12px 8px 12px; }}
#panelTitle {{ color: {c['strong']}; font-size: 16px; font-weight: 600; }}
#muted {{ color: {c['muted']}; }}
#bigTitle {{ color: {c['title']}; font-size: 17px; font-weight: 600; }}
#cardTitle {{ color: {c['card_text']}; font-size: 12px; }}
#hint {{ color: {c['hint']}; font-size: 12px; }}

QPushButton {{
    background: {c['btn']}; border: 1px solid {c['border2']}; border-radius: 7px;
    padding: 6px 14px; color: {c['text']};
}}
QPushButton:hover {{ background: {c['btn_hover']}; }}
QPushButton:pressed {{ background: {c['btn_pressed']}; }}
QPushButton:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['white']}; font-weight: 600; }}
QPushButton:disabled {{ color: {c['disabled']}; background: {c['btn_disabled']}; border-color: {c['btn_disabled_border']}; }}

QPushButton[cls="primary"] {{
    background: {c['accent']}; border: 1px solid {c['accent']}; color: {c['white']}; font-weight: 600;
}}
QPushButton[cls="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[cls="primary"]:disabled {{ background: {c['accent_disabled']}; border-color: {c['accent_disabled']}; color: {c['accent_disabled_text']}; }}

QPushButton[cls="ghost"] {{ background: transparent; border: 1px solid {c['ghost_border']}; color: {c['ghost_text']}; }}
QPushButton[cls="ghost"]:hover {{ background: {c['btn']}; }}

QPushButton[cls="danger"] {{ background: {c['danger_bg']}; border: 1px solid {c['danger_border']}; color: {c['danger_text']}; }}
QPushButton[cls="danger"]:hover {{ background: {c['danger_hover']}; }}

QPushButton[cls="nav"] {{
    background: transparent; border: none; border-radius: 8px;
    text-align: left; padding: 10px 14px; color: {c['nav_text']}; font-size: 14px;
}}
QPushButton[cls="nav"]:hover {{ background: {c['nav_hover']}; color: {c['strong']}; }}
QPushButton[cls="nav"]:checked {{ background: {c['nav_checked_bg']}; color: {c['nav_checked_text']}; font-weight: 600;
    border-left: 3px solid {c['accent']}; }}

QLineEdit, QSpinBox, QComboBox, QTextEdit {{
    background: {c['input']}; border: 1px solid {c['border2']}; border-radius: 7px; padding: 6px 9px;
    selection-background-color: {c['accent']}; selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}

/* 数字框：给右侧按钮留出位置并显式定位，否则 Fusion 的默认按钮
   会被自定义 padding/圆角挤成两个小点、还会戳出边框 */
QSpinBox {{ padding: 6px 26px 6px 9px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px;
    background: transparent; border: none; border-left: 1px solid {c['border2']};
}}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 1px 1px 0 0; border-top-right-radius: 6px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 1px 1px 0; border-bottom-right-radius: 6px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {c['btn_hover']}; }}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{ background: {c['btn_pressed']}; }}
QSpinBox::up-arrow {{ width: {ARROW_W}px; height: {ARROW_H}px; {up_img} }}
QSpinBox::down-arrow {{ width: {ARROW_W}px; height: {ARROW_H}px; {down_img} }}
QComboBox QAbstractItemView {{
    background: {c['popup']}; border: 1px solid {c['border2']}; border-radius: 6px;
    selection-background-color: {c['sel']}; selection-color: {c['text']};
}}

QListWidget {{ background: {c['list']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {c['btn']}; }}
QListWidget::item:selected {{ background: {c['sel']}; color: {c['text']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['scrollbar']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['scrollbar_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['scrollbar']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QProgressBar {{
    background: {c['input']}; border: 1px solid {c['progress_border']}; border-radius: 6px;
    text-align: center; color: {c['text']}; font-size: 11px; height: 16px;
}}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}

QFrame[cls="card"] {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame[cls="card"]:hover {{ border-color: {c['border2']}; }}
QFrame[cls="albumCard"] {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame[cls="albumCard"]:hover {{ border: 1px solid {c['accent']}; }}

QToolTip {{ background: {c['popup']}; color: {c['text']}; border: 1px solid {c['border2']}; padding: 4px 8px; }}
QMenu {{ background: {c['popup']}; border: 1px solid {c['border2']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['sel']}; }}
QStatusBar {{ background: {c['bg']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
'''


def apply_theme(app: QApplication, light: bool = False):
    c = LIGHT if light else DARK
    app.setStyle('Fusion')
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c['bg']))
    pal.setColor(QPalette.WindowText, QColor(c['text']))
    pal.setColor(QPalette.Base, QColor(c['list']))
    pal.setColor(QPalette.AlternateBase, QColor(c['card']))
    pal.setColor(QPalette.Text, QColor(c['text']))
    pal.setColor(QPalette.Button, QColor(c['btn']))
    pal.setColor(QPalette.ButtonText, QColor(c['text']))
    pal.setColor(QPalette.Highlight, QColor(c['accent']))
    pal.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    pal.setColor(QPalette.ToolTipBase, QColor(c['popup']))
    pal.setColor(QPalette.ToolTipText, QColor(c['text']))
    pal.setColor(QPalette.PlaceholderText, QColor(c['faint']))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c['disabled']))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c['disabled']))
    app.setPalette(pal)
    app.setStyleSheet(_qss(c, _arrow_urls(c)))
