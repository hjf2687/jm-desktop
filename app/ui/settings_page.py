"""设置页：主题、下载目录、接口、代理、账号、缓存管理。"""
import os

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..async_util import run_async
from ..cache import fmt_size


class SettingsPage(QWidget):
    saved = Signal()
    toast = Signal(str)

    def __init__(self, settings, cache=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.cache = cache
        self.setObjectName('settingsBody')

        title = QLabel('设置')
        title.setObjectName('panelTitle')

        form = QVBoxLayout()
        form.setSpacing(12)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem('暗色', 'dark')
        self.theme_combo.addItem('亮色', 'light')
        form.addLayout(self._labeled('主题', self.theme_combo))

        self.dl_dir = self._row('下载目录', form, QLineEdit(), browse=True)
        self.impl_combo = QComboBox()
        self.impl_combo.addItem('移动端接口（api）', 'api')
        self.impl_combo.addItem('网页端接口（html）', 'html')
        form.addLayout(self._labeled('接口类型', self.impl_combo))

        self.threads = QSpinBox()
        self.threads.setRange(1, 16)
        form.addLayout(self._labeled('图片并发线程', self.threads))

        self.retry = QSpinBox()
        self.retry.setRange(0, 10)
        form.addLayout(self._labeled('请求重试次数', self.retry))

        self.proxy = self._row('HTTP 代理', form, QLineEdit(),
                               placeholder='http://127.0.0.1:7890（留空＝直连）')
        self.domains = self._row('自定义域名', form, QLineEdit(),
                                 placeholder='逗号分隔，留空自动获取（仅 api 接口）')
        self.username = self._row('账号', form, QLineEdit(),
                                  placeholder='可选：用于下载需登录的本子')
        self.password = self._row('密码', form, QLineEdit(), password=True,
                                  placeholder='与账号配套使用')

        # -- 缓存管理 --
        cache_title = QLabel('缓存')
        cache_title.setObjectName('panelTitle')

        self.auto_evict = QCheckBox('超限时自动清理最久未使用的缓存')
        self.auto_evict.setToolTip(
            '按"最后访问时间"淘汰：封面逐张删，阅读缓存整本删。\n'
            '正在阅读和正在下载的本子会跳过。下载目录、收藏、阅读进度永不触碰。')

        self.cov_stat = QLabel('统计中…')
        self.cov_stat.setObjectName('hint')
        self.cov_limit = QSpinBox()
        self.cov_limit.setRange(0, 102400)
        self.cov_limit.setSpecialValueText('不限制')
        self.cov_limit.setFixedWidth(96)
        self.cov_unit = QLabel('MB')
        self.cov_unit.setObjectName('hint')
        self.cov_unit.setFixedWidth(22)

        self.reader_stat = QLabel('统计中…')
        self.reader_stat.setObjectName('hint')
        self.reader_limit = QSpinBox()
        self.reader_limit.setRange(0, 102400)
        self.reader_limit.setSpecialValueText('不限制')
        self.reader_limit.setFixedWidth(96)
        self.reader_unit = QLabel('MB')
        self.reader_unit.setObjectName('hint')
        self.reader_unit.setFixedWidth(22)

        refresh_btn = QPushButton('刷新占用')
        refresh_btn.setProperty('cls', 'ghost')
        clear_cov_btn = QPushButton('清空封面缓存')
        clear_cov_btn.setProperty('cls', 'ghost')
        clear_reader_btn = QPushButton('清空阅读缓存')
        clear_reader_btn.setProperty('cls', 'ghost')
        sweep_btn = QPushButton('立即清理超额')
        sweep_btn.setProperty('cls', 'ghost')

        cache_btns = QHBoxLayout()
        for b in (refresh_btn, clear_cov_btn, clear_reader_btn, sweep_btn):
            cache_btns.addWidget(b)
        cache_btns.addStretch(1)

        cache_note = QLabel('为控制体积，上限留 0 表示不限制。清理只作用于封面与阅读缓存，'
                            '下载的漫画和阅读进度不受影响。')
        cache_note.setObjectName('hint')
        cache_note.setWordWrap(True)

        btns = QHBoxLayout()
        save_btn = QPushButton('保存设置')
        save_btn.setProperty('cls', 'primary')
        data_btn = QPushButton('打开数据目录')
        data_btn.setProperty('cls', 'ghost')
        btns.addWidget(save_btn)
        btns.addWidget(data_btn)
        btns.addStretch(1)

        note = QLabel('保存后立即生效：主题即时切换，其余配置作用于新任务。')
        note.setObjectName('hint')

        body = QVBoxLayout()
        body.setContentsMargins(26, 22, 26, 30)
        body.setSpacing(14)
        body.addWidget(title)
        body.addLayout(form)
        body.addSpacing(4)
        body.addWidget(cache_title)
        body.addWidget(self.auto_evict)
        body.addLayout(self._cache_row('封面缓存', self.cov_stat, self.cov_limit, self.cov_unit))
        body.addLayout(self._cache_row('阅读缓存', self.reader_stat, self.reader_limit, self.reader_unit))
        body.addLayout(cache_btns)
        body.addWidget(cache_note)
        body.addSpacing(4)
        body.addLayout(btns)
        body.addWidget(note)
        body.addStretch(1)
        self.setLayout(body)

        save_btn.clicked.connect(self._save)
        data_btn.clicked.connect(self._open_data_dir)
        refresh_btn.clicked.connect(self.refresh_cache_stats)
        clear_cov_btn.clicked.connect(lambda: self._do_clear('covers'))
        clear_reader_btn.clicked.connect(lambda: self._do_clear('reader'))
        sweep_btn.clicked.connect(self._do_sweep)
        self.cov_limit.valueChanged.connect(self._sync_units)
        self.reader_limit.valueChanged.connect(self._sync_units)

        self.load_fields()
        self.refresh_cache_stats()

    # -- 表单构建 --
    def _labeled(self, text, widget):
        lab = QLabel(text)
        lab.setFixedWidth(110)
        lay = QHBoxLayout()
        lay.addWidget(lab)
        lay.addWidget(widget, 1)
        return lay

    def _cache_row(self, text, stat_label, limit_spin, unit_label):
        row = QHBoxLayout()
        lab = QLabel(text)
        lab.setFixedWidth(110)
        row.addWidget(lab)
        row.addWidget(stat_label, 1)
        cap = QLabel('上限')
        cap.setObjectName('hint')
        row.addWidget(cap)
        row.addWidget(limit_spin)
        row.addWidget(unit_label)      # 单位放框外，框里只留纯数字
        return row

    def _sync_units(self):
        """数值为 0 时框里显示"不限制"，此时不该再挂一个 MB 在边上。"""
        self.cov_unit.setVisible(self.cov_limit.value() > 0)
        self.reader_unit.setVisible(self.reader_limit.value() > 0)

    def _row(self, text, form, widget, browse=False, password=False,
             placeholder=''):
        if password:
            widget.setEchoMode(QLineEdit.Password)
        if placeholder:
            widget.setPlaceholderText(placeholder)
        row = QHBoxLayout()
        lab = QLabel(text)
        lab.setFixedWidth(110)
        row.addWidget(lab)
        row.addWidget(widget, 1)
        if browse:
            btn = QPushButton('浏览…')
            btn.setProperty('cls', 'ghost')
            btn.clicked.connect(self._browse_dir)
            row.addWidget(btn)
        form.addLayout(row)
        return widget

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择下载目录', self.settings['download_dir'])
        if d:
            self.dl_dir.setText(d)

    # -- 读写 --
    def load_fields(self):
        s = self.settings
        idx = self.theme_combo.findData(s['theme'])
        self.theme_combo.setCurrentIndex(max(0, idx))
        self.dl_dir.setText(s['download_dir'])
        idx = self.impl_combo.findData(s['client_impl'])
        self.impl_combo.setCurrentIndex(max(0, idx))
        self.threads.setValue(int(s['threads']))
        self.retry.setValue(int(s['retry_times']))
        self.proxy.setText(s['proxy'] or '')
        self.domains.setText(s['custom_domains'] or '')
        self.username.setText(s['username'] or '')
        self.password.setText(s['password'] or '')
        self.auto_evict.setChecked(bool(s['cache_auto_evict']))
        self.cov_limit.setValue(int(s['cache_cover_limit_mb']))
        self.reader_limit.setValue(int(s['cache_reader_limit_mb']))
        self._sync_units()      # 值未变化时不会触发信号，这里兜一次

    def _save(self):
        s = self.settings
        s['theme'] = self.theme_combo.currentData()
        s['download_dir'] = self.dl_dir.text().strip() or s['download_dir']
        s['client_impl'] = self.impl_combo.currentData()
        s['threads'] = self.threads.value()
        s['retry_times'] = self.retry.value()
        s['proxy'] = self.proxy.text().strip()
        s['custom_domains'] = self.domains.text().strip()
        s['username'] = self.username.text().strip()
        s['password'] = self.password.text()
        s['cache_auto_evict'] = self.auto_evict.isChecked()
        s['cache_cover_limit_mb'] = self.cov_limit.value()
        s['cache_reader_limit_mb'] = self.reader_limit.value()
        s.save()
        self.saved.emit()

    def _open_data_dir(self):
        from app import settings as settings_mod
        QDesktopServices.openUrl(QUrl.fromLocalFile(settings_mod.DATA_DIR))

    # -- 缓存统计与清理 --
    def refresh_cache_stats(self):
        if self.cache is None:
            self.cov_stat.setText('不可用')
            self.reader_stat.setText('不可用')
            return
        self.cov_stat.setText('统计中…')
        self.reader_stat.setText('统计中…')
        run_async(self.cache.stats, on_done=self._on_stats,
                  on_fail=lambda _e: self.cov_stat.setText('统计失败'))

    def _on_stats(self, st):
        self.cov_stat.setText(self._fmt_stat(st['covers']))
        self.reader_stat.setText(self._fmt_stat(st['reader']))

    @staticmethod
    def _fmt_stat(d):
        txt = '%s · %d 个文件' % (fmt_size(d['bytes']), d['files'])
        if d['limit'] and d['bytes'] > d['limit']:
            txt += ' · 已超限'
        return txt

    def _do_clear(self, which):
        if self.cache is None:
            return
        self.toast.emit('正在清理…')
        fn = self.cache.clear_covers if which == 'covers' else self.cache.clear_reader
        run_async(fn, on_done=lambda r, w=which: self._after_clear(w, r))

    def _after_clear(self, which, result):
        n, freed = result
        name = '封面缓存' if which == 'covers' else '阅读缓存'
        if n:
            self.toast.emit('已清理%s：%d 项，释放 %s' % (name, n, fmt_size(freed)))
        else:
            self.toast.emit('%s本来就没有内容' % name)
        self.refresh_cache_stats()

    def _do_sweep(self):
        if self.cache is None:
            return
        self.toast.emit('正在清理超额缓存…')
        run_async(self.cache.sweep, on_done=self._after_sweep)

    def _after_sweep(self, result):
        n = sum(v[0] for v in result.values())
        freed = sum(v[1] for v in result.values())
        if n:
            self.toast.emit('已清理 %d 项，释放 %s' % (n, fmt_size(freed)))
        else:
            self.toast.emit('缓存未超上限，无需清理')
        self.refresh_cache_stats()
