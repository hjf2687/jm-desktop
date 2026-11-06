"""JM 桌面端入口。"""
import os
import sys

# pythonw 下没有 stdout/stderr：stdout 丢弃，stderr 落到日志文件，便于排查
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
if sys.stderr is None:
    from app import settings as settings_mod
    os.makedirs(settings_mod.DATA_DIR, exist_ok=True)
    sys.stderr = open(os.path.join(settings_mod.DATA_DIR, 'error.log'), 'a',
                      encoding='utf-8', errors='replace')

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from app.settings import Settings
from app.theme import apply_theme
from app.ui.main_window import MainWindow


def adult_gate(settings: Settings) -> bool:
    if settings['adult_ack']:
        return True
    dlg = QDialog()
    dlg.setWindowTitle('年龄确认')
    dlg.setModal(True)
    dlg.setFixedSize(460, 230)

    title = QLabel('本应用内容仅限成年人')
    title.setObjectName('panelTitle')
    text = QLabel(
        'JM 桌面端用于在线浏览、下载禁漫天堂（JM Comic）的漫画内容。\n\n'
        '请确认您已年满 18 周岁，且遵守所在地的法律法规。'
    )
    text.setWordWrap(True)
    text.setObjectName('muted')

    enter_btn = QPushButton('我已满 18 岁，进入')
    enter_btn.setProperty('cls', 'primary')
    quit_btn = QPushButton('退出')

    btns = QHBoxLayout()
    btns.addStretch(1)
    btns.addWidget(quit_btn)
    btns.addWidget(enter_btn)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 22, 24, 20)
    lay.setSpacing(14)
    lay.addWidget(title)
    lay.addWidget(text)
    lay.addStretch(1)
    lay.addLayout(btns)

    def on_enter():
        settings['adult_ack'] = True
        settings.save()
        dlg.accept()

    enter_btn.clicked.connect(on_enter)
    quit_btn.clicked.connect(dlg.reject)
    return dlg.exec() == QDialog.Accepted


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('JM桌面端')

    settings = Settings()
    if not os.environ.get('JM_SKIP_GATE') and not adult_gate(settings):
        return 0
    apply_theme(app, settings['theme'] == 'light')

    win = MainWindow()
    win.show()

    auto_close = os.environ.get('JM_AUTOCLOSE_MS')
    if auto_close:
        QTimer.singleShot(int(auto_close), app.quit)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
