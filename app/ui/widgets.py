"""通用控件：可点击的封面卡片 + 响应式网格 + 自动换行布局。"""
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel, QLayout, QScrollArea,
                               QVBoxLayout, QWidget)

CARD_W, CARD_H = 190, 280
COVER_W, COVER_H = 190, 244


class FlowLayout(QLayout):
    """卡片自动换行布局（标准 Qt 实现，去掉 QtWidgets.QSizePolicy 依赖）。"""

    def __init__(self, parent=None, hspacing=12, vspacing=14):
        super().__init__(parent)
        self._items = []
        self._h = hspacing
        self._v = vspacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test_only):
        x, y = rect.x(), rect.y()
        line_h = 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._v
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class AlbumCard(QFrame):
    clicked = Signal(str)          # aid
    context_requested = Signal(str)  # aid（右键）

    def __init__(self, aid, title, parent=None):
        super().__init__(parent)
        self.setProperty('cls', 'albumCard')
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self.aid = aid

        self._cover = QLabel(self)
        self._cover.setFixedSize(COVER_W, COVER_H)
        self._cover.setAlignment(Qt.AlignCenter)
        self._cover.setStyleSheet('background:#17181d; border-radius:8px; color:#7d8491;')

        self._title = QLabel(self)
        self._title.setObjectName('cardTitle')
        self._title.setFixedWidth(CARD_W - 12)
        fm = self._title.fontMetrics()
        self._title.setText(fm.elidedText(title or aid, Qt.ElideRight, CARD_W - 12))
        self._title.setToolTip(title or aid)

        # 阅读进度角标（叠在封面底部）
        self._progress = QLabel(self._cover)
        self._progress.setAlignment(Qt.AlignCenter)
        self._progress.setFixedWidth(COVER_W)
        self._progress.setStyleSheet(
            'background:rgba(0,0,0,160); color:#fff;'
            'border-bottom-left-radius:8px; border-bottom-right-radius:8px;'
            'padding:2px 4px; font-size:11px;')
        self._progress.setGeometry(0, COVER_H - 24, COVER_W, 24)
        self._progress.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(6)
        lay.addWidget(self._cover, 0, Qt.AlignHCenter)
        lay.addWidget(self._title, 0, Qt.AlignHCenter)

    def set_cover(self, path):
        if not path:
            return
        try:
            pm = QPixmap(path)
            if pm.isNull():
                return
            pm = pm.scaled(COVER_W, COVER_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._cover.setPixmap(pm)
        except Exception:
            pass

    def set_progress(self, text):
        """显示/隐藏阅读进度角标（空文字隐藏）。"""
        if text:
            self._progress.setText(text)
            self._progress.show()
        else:
            self._progress.hide()

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit(self.aid)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.context_requested.emit(self.aid)
        event.accept()


class SearchGrid(QScrollArea):
    """自动按宽度分列的卡片网格。

    封面按需加载：只有滚进可视区（上下各多留一些余量）的卡片才去找封面。
    一页 80 条时，以前会一口气发出 80 个封面请求，现在首屏只发十几个，
    剩下的滚动时才补 —— 请求总数反而更少，首屏也更快。
    """

    COVER_MARGIN = 300   # 可视区外提前加载的余量（像素）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(18, 12, 18, 20)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setWidget(self._container)
        self._cards = []
        self._cols = 4
        # 封面懒加载
        self._cover_provider = None
        self._cover_requested = set()
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(50)   # 防抖：连续加卡片/滚动时只跑一次
        self._cover_timer.timeout.connect(self._load_visible_covers)
        self.verticalScrollBar().valueChanged.connect(self._schedule_cover_load)

    def set_cover_provider(self, fn):
        """注册封面加载回调：fn(aid) 会在卡片滚入可视区时被调用一次。"""
        self._cover_provider = fn
        self._schedule_cover_load()

    def cards(self):
        return list(self._cards)

    def clear(self):
        for c in self._cards:
            self._grid.removeWidget(c)
            c.deleteLater()
        self._cards.clear()
        self._cover_requested.clear()
        self._cover_timer.stop()

    def add_card(self, card):
        self._cards.append(card)
        self._relayout()
        self._schedule_cover_load()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_cover_load()

    # -- 封面懒加载 --
    def _schedule_cover_load(self):
        # 节流而不是防抖：滚动事件密集时若每次都重启定时器，平滑滚动
        # （触控板）下定时器会一直被推迟、永远不触发。这里改成"没在跑就启动"。
        if self._cover_provider is not None and not self._cover_timer.isActive():
            self._cover_timer.start()

    def _load_visible_covers(self):
        if self._cover_provider is None or not self._cards:
            return
        # 刚加进来的卡片要先跑一次布局，否则 geometry 还是旧的
        self._grid.activate()
        vp = self.viewport()
        top = self.verticalScrollBar().value()
        area = QRect(0, top - self.COVER_MARGIN,
                     vp.width(), vp.height() + 2 * self.COVER_MARGIN)
        for card in self._cards:
            if card.aid in self._cover_requested:
                continue
            if card.geometry().intersects(area):
                self._cover_requested.add(card.aid)
                self._cover_provider(card.aid)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.viewport().width()
        cols = max(3, (w - 36 + 12) // (CARD_W + 12))
        if cols != self._cols:
            self._cols = cols
            self._relayout()
        self._schedule_cover_load()

    def _relayout(self):
        while self._grid.count():
            self._grid.takeAt(0)
        for i, card in enumerate(self._cards):
            r, c = divmod(i, self._cols)
            self._grid.addWidget(card, r, c)
