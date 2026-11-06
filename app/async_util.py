"""把函数丢进 QThreadPool 后台执行，结果通过 Qt 信号回到主线程。"""
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

POOL = QThreadPool.globalInstance()
POOL.setMaxThreadCount(16)

# 常驻对象：作为 TaskSignals 的 parent。
# 无主 QObject 会被 Python 垃圾回收提前销毁，导致后台任务完成时
# emit 报 "Signal source has been deleted"，信号丢失（图片/结果不显示）。
_KEEPER = QObject()


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn, on_done=None, on_fail=None):
        super().__init__()
        self.fn = fn
        self.signals = TaskSignals(_KEEPER)
        if on_done is not None:
            self.signals.done.connect(on_done)
        if on_fail is not None:
            self.signals.failed.connect(on_fail)

    def run(self):
        try:
            result = self.fn()
        except Exception as e:
            traceback.print_exc()
            self._safe_emit(self.signals.failed, str(e))
        else:
            self._safe_emit(self.signals.done, result)

    @staticmethod
    def _safe_emit(signal, value):
        # 应用退出时 QApplication 已销毁，此时 emit 会抛
        # "Signal source has been deleted"，静默忽略即可。
        try:
            signal.emit(value)
        except RuntimeError:
            pass


def run_async(fn, on_done=None, on_fail=None):
    POOL.start(Task(fn, on_done, on_fail))
