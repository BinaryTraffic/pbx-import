import sys
import os
import re
from datetime import datetime
from dotenv import load_dotenv
import requests as req_lib

os.environ.setdefault('QT_API', 'pyside6')
import matplotlib
matplotlib.use('QtAgg')
matplotlib.rcParams['font.family'] = ['MS Gothic', 'Yu Gothic', 'sans-serif']
matplotlib.rcParams['axes.facecolor']  = '#161b22'
matplotlib.rcParams['figure.facecolor'] = '#0d1117'
matplotlib.rcParams['text.color']      = '#e6edf3'
matplotlib.rcParams['axes.labelcolor'] = '#8b949e'
matplotlib.rcParams['xtick.color']     = '#8b949e'
matplotlib.rcParams['ytick.color']     = '#8b949e'
matplotlib.rcParams['axes.edgecolor']  = '#30363d'
matplotlib.rcParams['grid.color']      = '#21262d'
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QTextEdit, QLabel, QComboBox, QSpinBox,
    QGroupBox, QMessageBox, QSizePolicy, QFrame, QProgressBar,
    QTableWidget, QTableWidgetItem, QScrollArea, QHeaderView
)
from PySide6.QtCore import Qt, QProcess, Signal, Slot, QProcessEnvironment, QThread
from PySide6.QtGui import QColor, QFont, QTextCursor

_TQDM_RE  = re.compile(r'([^:\r\n|%]+):\s*(\d+)%\|.*?\|\s*(\d+)/(\d+)')
_UNIT_RE  = re.compile(r'[\d.]+(\S+)/s')

# ── パス解決 ──────────────────────────────────────────
if getattr(sys, 'frozen', False):
    DIST_DIR = os.path.dirname(sys.executable)
else:
    DIST_DIR = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dist')
    )

_exe_self = os.path.basename(sys.executable) if getattr(sys, 'frozen', False) else ''
IS_BETA = '_beta' in _exe_self

def exe(base):
    """beta実行時は *_beta.exe を使う"""
    return f"{base}_beta.exe" if IS_BETA else f"{base}.exe"

# ── タスク定義 ─────────────────────────────────────────
BRANCH_MAP = {-1: '全支店', 0: '八幡山店', 1: '芝店', 2: '目黒店'}

TASK_DEFS = {
    'ohp_all': {
        'label': '全支店一括実行',
        'group': 'OHP',
        'icon': '🔄',
        'exe': 'sc_cal_all',
        'params': [],
        'desc': '全3支店のカレンダー＋ユーザーを一括取得（タスクスケジューラーと同等）',
    },
    'ohp_recovery': {
        'label': 'カレンダーリカバリー',
        'group': 'OHP',
        'icon': '📅',
        'exe': 'oh_cal_import_db_sc',
        'params': ['branch', 'month'],
        'desc': '取り漏らし時の手動リカバリー。支店・月数を指定して実行。',
    },
    'pbx_sync': {
        'label': 'PBX同期',
        'group': 'PBX',
        'icon': '📞',
        'exe': 'pbx_sync_all',
        'params': [],
        'desc': 'DBから新規メンバーをエクスポートし、PBXアドレス帳を更新する。',
    },
}

# ── ダッシュボード定数 ─────────────────────────────────
DASHBOARD_TABLES = [
    ('v_7days_after_visit',         '来店から7日目'),
    ('v_pets_birthdays_today',      '今日誕生日 🎂'),
    ('v_pets_next_month_birthdays', '来月誕生日 🎂'),
    ('v_present_month_entry',       '今月の新規'),
]
DASHBOARD_CHARTS = [
    ('v_montly_new_registrants_score_all',          '月別新規（全体）',    '月',  '新規件数', False),
    ('v_montly_new_registrants_score_with_blanch',  '月別新規（支店別）',  '月',  '新規件数', True),
    ('v_weekly_visit_score_all',                    '曜日来店数（全体）',  '曜日','来店数',  False),
    ('v_weekly_visit_score_with_blanch',            '曜日来店数（支店別）','曜日','来店数',  True),
]
WEEKDAY_JA = {'Monday':'月','Tuesday':'火','Wednesday':'水',
              'Thursday':'木','Friday':'金','Saturday':'土','Sunday':'日'}
WEEKDAY_ORDER = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
BRANCH_COLORS = {'八幡山店':'#f87171','芝店':'#60a5fa','目黒店':'#fbbf24'}

# ── ログウィジェット ───────────────────────────────────
class LogWidget(QTextEdit):

    RULES = [
        ('[ERROR]',   '#ff6b6b'),
        ('ERROR',     '#ff6b6b'),
        ('❌',        '#ff6b6b'),
        ('Traceback', '#ff6b6b'),
        ('[WARNING]', '#ffa94d'),
        ('WARNING',   '#ffa94d'),
        ('✅',        '#69db7c'),
        ('完了',      '#69db7c'),
        ('[INFO]',    '#a8c7fa'),
    ]

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont('Consolas', 9))
        self.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #e6edf3;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 4px;
            }
        """)

    def append_log(self, text: str):
        if not text.strip():
            return
        color = '#e6edf3'
        for keyword, c in self.RULES:
            if keyword in text:
                color = c
                break
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.setTextColor(QColor(color))
        self.insertPlainText(text + '\n')
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_log(self):
        self.clear()


# ── API ワーカー ──────────────────────────────────────
class ApiWorker(QThread):
    result_ready = Signal(str, list)
    error        = Signal(str, str)

    def __init__(self, url: str, user: str, pw: str, view: str):
        super().__init__()
        self._url  = url
        self._user = user
        self._pw   = pw
        self._view = view

    def run(self):
        try:
            res  = req_lib.get(f'{self._url}/store/{self._view}',
                               auth=(self._user, self._pw), timeout=15)
            data = res.json().get('data', [])
            self.result_ready.emit(self._view, data)
        except Exception as e:
            self.error.emit(self._view, str(e))


# ── ダッシュボードパネル ───────────────────────────────
class DashboardPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[ApiWorker] = []
        self._chart_data: dict         = {}
        self._pending: set             = set()
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        # 更新ボタン
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton('🔄  更新')
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.clicked.connect(self._refresh)
        self._updated_lbl = QLabel('')
        self._updated_lbl.setStyleSheet('color:#8b949e; font-size:11px;')
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._updated_lbl)
        root.addLayout(btn_row)

        # スクロール領域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea{border:none;}')
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(12)

        # ── チャートグリッド ──
        chart_grp = QGroupBox('グラフ')
        chart_lay = QGridLayout(chart_grp)
        chart_lay.setSpacing(8)
        self._fig = Figure(figsize=(10, 5), tight_layout=True)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setMinimumHeight(380)
        chart_lay.addWidget(self._canvas, 0, 0)
        cl.addWidget(chart_grp)

        # ── テーブル ──
        self._table_widgets: dict = {}
        for view, label in DASHBOARD_TABLES:
            grp = QGroupBox(label)
            gl  = QVBoxLayout(grp)
            tbl = QTableWidget()
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.setMaximumHeight(180)
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            tbl.setStyleSheet("""
                QTableWidget       { background:#161b22; color:#e6edf3;
                                     border:1px solid #30363d; gridline-color:#21262d; font-size:11px; }
                QHeaderView::section { background:#21262d; color:#8b949e;
                                       border:1px solid #30363d; padding:3px; font-size:11px; }
                QTableWidget::item:selected { background:#1f6feb; }
            """)
            gl.addWidget(tbl)
            cl.addWidget(grp)
            self._table_widgets[view] = tbl

        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

    # ── データ取得 ──
    def _refresh(self):
        self.refresh_btn.setEnabled(False)
        url  = os.getenv('OH_DASH_URL',  'https://oh.binarytraffic.jp')
        user = os.getenv('OH_DASH_USER', '')
        pw   = os.getenv('OH_DASH_PASS', '')

        all_views = ([v for v, _ in DASHBOARD_TABLES] +
                     [v for v, *_ in DASHBOARD_CHARTS])
        self._pending   = set(all_views)
        self._chart_data = {}
        self._workers   = []

        for view in all_views:
            w = ApiWorker(url, user, pw, view)
            w.result_ready.connect(self._on_data)
            w.error.connect(self._on_error)
            w.finished.connect(self._check_done)
            self._workers.append(w)
            w.start()

    @Slot(str, list)
    def _on_data(self, view: str, data: list):
        tbl = self._table_widgets.get(view)
        if tbl is not None:
            self._fill_table(tbl, data)
        for cfg in DASHBOARD_CHARTS:
            if cfg[0] == view:
                self._chart_data[view] = data
        self._pending.discard(view)

    @Slot(str, str)
    def _on_error(self, view: str, msg: str):
        tbl = self._table_widgets.get(view)
        if tbl:
            tbl.setColumnCount(1); tbl.setRowCount(1)
            tbl.setHorizontalHeaderLabels(['エラー'])
            tbl.setItem(0, 0, QTableWidgetItem(msg))
        self._pending.discard(view)

    @Slot()
    def _check_done(self):
        if not self._pending:
            self._draw_charts()
            self.refresh_btn.setEnabled(True)
            self._updated_lbl.setText(f'最終更新: {datetime.now().strftime("%H:%M:%S")}')

    def _fill_table(self, tbl: QTableWidget, data: list):
        if not data:
            tbl.setRowCount(0); tbl.setColumnCount(1)
            tbl.setHorizontalHeaderLabels(['データなし'])
            return
        headers = list(data[0].keys())
        tbl.setColumnCount(len(headers))
        tbl.setRowCount(len(data))
        tbl.setHorizontalHeaderLabels(headers)
        for r, row in enumerate(data):
            for c, k in enumerate(headers):
                tbl.setItem(r, c, QTableWidgetItem(
                    str(row[k]) if row[k] is not None else ''))
        tbl.resizeColumnsToContents()

    # ── チャート描画 ──
    def _draw_charts(self):
        self._fig.clear()
        for i, (view, title, x_key, y_key, stacked) in enumerate(DASHBOARD_CHARTS):
            ax = self._fig.add_subplot(2, 2, i + 1)
            ax.set_title(title, fontsize=9, pad=4)
            data = self._chart_data.get(view, [])
            if not data:
                ax.text(0.5, 0.5, 'データなし', ha='center', va='center', fontsize=9)
                continue
            if stacked:
                self._draw_stacked(ax, data, x_key, y_key)
            else:
                self._draw_simple(ax, data, x_key, y_key)
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw()

    def _draw_simple(self, ax, data, x_key, y_key):
        if x_key == '月':
            data = [r for r in data if str(r.get(x_key, '')) >= '2024-09']
        labels = [WEEKDAY_JA.get(str(r.get(x_key, '')), str(r.get(x_key, ''))) for r in data]
        values = [float(r.get(y_key, 0) or 0) for r in data]
        ax.bar(labels, values, color='#3b82f6', alpha=0.8)
        ax.tick_params(axis='x', rotation=45, labelsize=7)
        ax.tick_params(axis='y', labelsize=7)

    def _draw_stacked(self, ax, data, x_key, y_key):
        if x_key == '月':
            data = [r for r in data if str(r.get(x_key, '')) >= '2024-09']
        label_set = sorted(set(str(r[x_key]) for r in data),
                           key=lambda x: WEEKDAY_ORDER.index(x) if x in WEEKDAY_ORDER else x)
        branches  = list(dict.fromkeys(str(r.get('支店名', '')) for r in data))
        labels_jp = [WEEKDAY_JA.get(l, l) for l in label_set]
        bottom    = [0.0] * len(label_set)
        for branch in branches:
            d_map  = {str(r[x_key]): float(r.get(y_key, 0) or 0)
                      for r in data if str(r.get('支店名', '')) == branch}
            values = [d_map.get(l, 0.0) for l in label_set]
            ax.bar(labels_jp, values, bottom=bottom,
                   label=branch, color=BRANCH_COLORS.get(branch, '#888'), alpha=0.85)
            bottom = [b + v for b, v in zip(bottom, values)]
        ax.legend(fontsize=7, facecolor='#21262d', edgecolor='#30363d', labelcolor='#e6edf3')
        ax.tick_params(axis='x', rotation=45, labelsize=7)
        ax.tick_params(axis='y', labelsize=7)


# ── タスクパネル ──────────────────────────────────────
class TaskPanel(QWidget):

    status_changed = Signal(str, str)   # task_id, 'running'|'done'|'error'|'stopped'

    def __init__(self, task_id: str, config: dict, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.config  = config
        self.process: QProcess | None = None
        self._pending: list[list[str]] = []   # 全支店実行時のキュー
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── 説明 ──
        desc = QLabel(self.config.get('desc', ''))
        desc.setWordWrap(True)
        desc.setStyleSheet('color: #8b949e; font-size: 11px;')
        root.addWidget(desc)

        # ── 操作パネル ──
        ctrl = QGroupBox('操作パネル')
        ctrl_lay = QVBoxLayout(ctrl)
        ctrl_lay.setSpacing(8)

        self._param_widgets: dict = {}

        if 'branch' in self.config.get('params', []):
            row = QHBoxLayout()
            row.addWidget(QLabel('支店:'))
            cb = QComboBox()
            for idx, name in BRANCH_MAP.items():
                cb.addItem(name, idx)
            cb.setFixedWidth(130)
            self._param_widgets['branch'] = cb
            row.addWidget(cb)
            row.addStretch()
            ctrl_lay.addLayout(row)

        if 'month' in self.config.get('params', []):
            row = QHBoxLayout()
            row.addWidget(QLabel('遡り月数:'))
            sp = QSpinBox()
            sp.setRange(0, 12)
            sp.setValue(0)
            sp.setSuffix(' ヶ月')
            sp.setFixedWidth(100)
            self._param_widgets['month'] = sp
            row.addWidget(sp)
            row.addStretch()
            ctrl_lay.addLayout(row)

        # ボタン行
        btn_row = QHBoxLayout()

        self.run_btn = QPushButton('▶  実行')
        self.run_btn.setFixedHeight(34)
        self.run_btn.setStyleSheet(self._btn_style('#1f6feb', '#388bfd'))
        self.run_btn.clicked.connect(self._run)

        self.stop_btn = QPushButton('■  停止')
        self.stop_btn.setFixedHeight(34)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(self._btn_style('#da3633', '#f85149'))
        self.stop_btn.clicked.connect(self._stop)

        self.status_lbl = QLabel('待機中')
        self.status_lbl.setStyleSheet('color: #8b949e; font-weight: bold;')

        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.status_lbl)
        ctrl_lay.addLayout(btn_row)

        # プログレスバー（tqdm検出時のみ表示）
        _bar_style = """
            QProgressBar { background: #21262d; border: 1px solid #30363d; border-radius: 3px; }
            QProgressBar::chunk { background: %s; border-radius: 3px; }
        """
        self._prog_widgets: dict = {}
        for key, name, color in [
            ('branch', '支店進捗',   '#1f6feb'),
            ('step',   '処理ステップ', '#3fb950'),
        ]:
            row = QHBoxLayout()
            name_lbl = QLabel(name)
            name_lbl.setFixedWidth(120)
            name_lbl.setStyleSheet('color: #8b949e; font-size: 11px;')
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFixedHeight(14)
            bar.setTextVisible(False)
            bar.setStyleSheet(_bar_style % color)
            count_lbl = QLabel('')
            count_lbl.setStyleSheet('color: #8b949e; font-size: 11px; min-width: 70px;')
            row.addWidget(name_lbl)
            row.addWidget(bar, stretch=1)
            row.addWidget(count_lbl)
            rw = QWidget()
            rw.setLayout(row)
            rw.setVisible(False)
            ctrl_lay.addWidget(rw)
            self._prog_widgets[key] = (rw, bar, count_lbl)

        root.addWidget(ctrl)

        # ── ログ ──
        log_group = QGroupBox('ログ出力')
        log_lay = QVBoxLayout(log_group)
        log_lay.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.addStretch()
        clr = QPushButton('クリア')
        clr.setFixedSize(56, 22)
        clr.setStyleSheet('font-size: 11px;')
        hdr.addWidget(clr)
        log_lay.addLayout(hdr)

        self.log = LogWidget()
        log_lay.addWidget(self.log)
        clr.clicked.connect(self.log.clear_log)

        root.addWidget(log_group, stretch=1)

    # ── ボタンスタイル ──
    @staticmethod
    def _btn_style(base: str, hover: str) -> str:
        return f"""
            QPushButton {{
                background-color: {base};
                color: white; border: none;
                border-radius: 6px; font-weight: bold;
                padding: 0 20px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:disabled {{ background-color: #21262d; color: #484f58; }}
        """

    # ── 実行引数の構築 ──
    def _build_args_list(self) -> list[list[str]]:
        """全支店の場合は branch=0,1,2 を順番にキューとして返す"""
        branch_val = -1
        month_val  = 0

        if 'branch' in self._param_widgets:
            branch_val = self._param_widgets['branch'].currentData()
        if 'month' in self._param_widgets:
            month_val = self._param_widgets['month'].value()

        if branch_val == -1:
            return [
                [f'branch={b}', f'month={month_val}']
                for b in [0, 1, 2]
            ]
        else:
            args = [f'branch={branch_val}']
            if 'month' in self._param_widgets:
                args.append(f'month={month_val}')
            return [args]

    # ── 実行 ──
    def _run(self):
        exe_path = os.path.join(DIST_DIR, exe(self.config['exe']))
        if not os.path.exists(exe_path):
            self.log.append_log(f'[ERROR] EXEが見つかりません: {exe_path}')
            return

        self._pending = self._build_args_list()
        self._queue_total = len(self._pending)
        self._queue_done  = 0
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_status('実行中...', '#e3b341')
        self.status_changed.emit(self.task_id, 'running')
        self._start_next()

    def _start_next(self):
        if not self._pending:
            self._on_all_done(success=True)
            return

        args = self._pending.pop(0)
        exe_path = os.path.join(DIST_DIR, exe(self.config['exe']))

        # 支店進捗バーを開始時にインクリメント
        if self._queue_total > 1:
            current = self._queue_total - len(self._pending)
            pct = int(current / self._queue_total * 100)
            rw, bar, lbl = self._prog_widgets['branch']
            rw.setVisible(True)
            bar.setValue(pct)
            lbl.setText(f'{current} / {self._queue_total} 支店')

        ts = datetime.now().strftime('%H:%M:%S')
        self.log.append_log(
            f'\n[{ts}] ▶ {os.path.basename(exe_path)} {" ".join(args)}'
        )

        # 処理ステップバーをリセット
        rw, bar, lbl = self._prog_widgets['step']
        bar.setValue(0)
        lbl.setText('')
        rw.setVisible(False)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(DIST_DIR)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._on_stdout)
        self.process.readyReadStandardError.connect(self._on_stderr)
        self.process.finished.connect(self._on_proc_finished)
        self.process.start(exe_path, args)

    # ── プロセスイベント ──
    @Slot()
    def _on_stdout(self):
        raw = self.process.readAllStandardOutput().data()
        for line in raw.decode('utf-8', errors='replace').splitlines():
            if '[DEBUG]' not in line:
                self.log.append_log(line)

    @Slot()
    def _on_stderr(self):
        raw = self.process.readAllStandardError().data()
        for line in raw.decode('utf-8', errors='replace').splitlines():
            if not line.strip():
                continue
            m = _TQDM_RE.search(line)
            if m:
                desc = m.group(1).strip()
                pct, cur, total = int(m.group(2)), m.group(3), m.group(4)
                u = _UNIT_RE.search(line)
                unit = u.group(1) if u else 'ステップ'
                rw, bar, count_lbl = self._prog_widgets['step']
                rw.setVisible(True)
                bar.setValue(pct)
                count_lbl.setText(f'{cur} / {total} {unit}')
            else:
                self.log.append_log(f'[ERROR] {line}')

    @Slot(int, QProcess.ExitStatus)
    def _on_proc_finished(self, code, status):
        ts = datetime.now().strftime('%H:%M:%S')
        if code == 0:
            self.log.append_log(f'[{ts}] ✅ 完了 (code={code})')
            self._start_next()
        else:
            self.log.append_log(f'[{ts}] ❌ 異常終了 (code={code})')
            self._pending.clear()
            self._on_all_done(success=False)

    def _on_all_done(self, success: bool):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if success:
            for rw, bar, lbl in self._prog_widgets.values():
                if rw.isVisible():
                    bar.setValue(100)
                    lbl.setText('完了')
        ts = datetime.now().strftime('%H:%M:%S')
        if success:
            self._set_status('完了 ✅', '#3fb950')
            self.log.append_log(f'[{ts}] === すべての処理が完了しました ===')
            self.status_changed.emit(self.task_id, 'done')
        else:
            self._set_status('エラー ❌', '#f85149')
            self.status_changed.emit(self.task_id, 'error')

    # ── 停止 ──
    def _stop(self):
        self._pending.clear()
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        ts = datetime.now().strftime('%H:%M:%S')
        self.log.append_log(f'[{ts}] [WARNING] ユーザーにより停止されました')
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._set_status('停止', '#8b949e')
        self.status_changed.emit(self.task_id, 'stopped')

    def _set_status(self, text: str, color: str = '#8b949e'):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f'color: {color}; font-weight: bold;')

    def is_running(self) -> bool:
        return (
            self.process is not None
            and self.process.state() != QProcess.ProcessState.NotRunning
        ) or bool(self._pending)


# ── メインウィンドウ ──────────────────────────────────
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        mode = ' [BETA]' if IS_BETA else ''
        self.setWindowTitle(f'OHP Sync Manager{mode}')
        self.setMinimumSize(1050, 680)
        self._open_tabs: dict[str, int] = {}   # task_id → tab index
        self._setup_ui()
        self._apply_style()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左ペイン ──────────────────────────
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(180)
        self.tree.setMaximumWidth(240)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)

        groups: dict[str, QTreeWidgetItem] = {}
        for task_id, cfg in TASK_DEFS.items():
            g = cfg['group']
            if g not in groups:
                gi = QTreeWidgetItem(self.tree, [f'  {g}'])
                gi.setExpanded(True)
                gi.setData(0, Qt.ItemDataRole.UserRole, None)
                groups[g] = gi
            child = QTreeWidgetItem(groups[g], [f'{cfg["icon"]}  {cfg["label"]}'])
            child.setData(0, Qt.ItemDataRole.UserRole, task_id)
            child.setToolTip(0, cfg.get('desc', ''))

        # ダッシュボード
        dash_item = QTreeWidgetItem(self.tree, ['  ダッシュボード'])
        dash_item.setData(0, Qt.ItemDataRole.UserRole, '__dashboard__')
        dash_item.setToolTip(0, 'モニタリングダッシュボード')

        self.tree.itemClicked.connect(self._on_tree_click)

        # ── 右ペイン（タブ）──────────────────
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)

        # ウェルカム画面
        welcome = QLabel(
            '<h3 style="color:#58a6ff">OHP Sync Manager</h3>'
            '<p style="color:#8b949e">← 左のメニューからタスクを選択してください</p>'
        )
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabs.addTab(welcome, 'ホーム')
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)

        splitter.addWidget(self.tree)
        splitter.addWidget(self.tabs)
        splitter.setSizes([200, 850])
        lay.addWidget(splitter)

    # ── ツリークリック ──
    @Slot(QTreeWidgetItem, int)
    def _on_tree_click(self, item: QTreeWidgetItem, _col: int):
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not task_id:
            return

        # ダッシュボード
        if task_id == '__dashboard__':
            if '__dashboard__' in self._open_tabs:
                self.tabs.setCurrentIndex(self._open_tabs['__dashboard__'])
                return
            panel = DashboardPanel()
            idx   = self.tabs.addTab(panel, '📊 ダッシュボード')
            self.tabs.setCurrentIndex(idx)
            self._open_tabs['__dashboard__'] = idx
            return

        # 既存タブにフォーカス（重複起動防止）
        if task_id in self._open_tabs:
            self.tabs.setCurrentIndex(self._open_tabs[task_id])
            return

        cfg   = TASK_DEFS[task_id]
        panel = TaskPanel(task_id, cfg)
        panel.status_changed.connect(self._on_status_changed)

        label = f'{cfg["icon"]} {cfg["label"]}'
        idx   = self.tabs.addTab(panel, label)
        self.tabs.setCurrentIndex(idx)
        self._open_tabs[task_id] = idx

    # ── タブを閉じる ──
    @Slot(int)
    def _close_tab(self, index: int):
        widget = self.tabs.widget(index)
        if isinstance(widget, DashboardPanel):
            self._open_tabs.pop('__dashboard__', None)
            for tid in list(self._open_tabs):
                if self._open_tabs[tid] > index:
                    self._open_tabs[tid] -= 1
            self.tabs.removeTab(index)
            return
        if not isinstance(widget, TaskPanel):
            return

        if widget.is_running():
            reply = QMessageBox.question(
                self, '確認',
                'タスクが実行中です。停止して閉じますか？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return
            widget._stop()

        task_id = widget.task_id
        self._open_tabs.pop(task_id, None)

        # 閉じたタブより後ろのインデックスを補正
        for tid in list(self._open_tabs):
            if self._open_tabs[tid] > index:
                self._open_tabs[tid] -= 1

        self.tabs.removeTab(index)

    # ── ステータス変化 → タブ名更新 ──
    @Slot(str, str)
    def _on_status_changed(self, task_id: str, status: str):
        if task_id not in self._open_tabs:
            return
        idx  = self._open_tabs[task_id]
        icon = TASK_DEFS[task_id]['icon']
        name = TASK_DEFS[task_id]['label']
        badges = {'running': '⏳', 'done': '✅', 'error': '❌', 'stopped': '⏹'}
        badge  = badges.get(status, icon)
        self.tabs.setTabText(idx, f'{badge} {name}')

    # ── スタイル ──
    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget   { background-color: #0d1117; color: #e6edf3; }
            QSplitter::handle      { background: #21262d; width: 1px; }
            QTreeWidget {
                background-color: #161b22;
                color: #c9d1d9;
                border: none;
                font-size: 13px;
            }
            QTreeWidget::item               { padding: 6px 4px; border-radius: 4px; }
            QTreeWidget::item:selected      { background-color: #1f6feb; color: white; }
            QTreeWidget::item:hover         { background-color: #21262d; }
            QTabWidget::pane               { border: none; background: #0d1117; }
            QTabBar::tab {
                background: #161b22; color: #8b949e;
                padding: 7px 18px; border: none;
                border-right: 1px solid #21262d;
                font-size: 12px;
            }
            QTabBar::tab:selected  { background: #0d1117; color: #e6edf3; border-top: 2px solid #1f6feb; }
            QTabBar::tab:hover     { background: #21262d; color: #c9d1d9; }
            QGroupBox {
                border: 1px solid #30363d; border-radius: 6px;
                margin-top: 10px; padding-top: 10px;
                color: #8b949e; font-weight: bold; font-size: 12px;
            }
            QGroupBox::title       { subcontrol-origin: margin; left: 10px; }
            QComboBox, QSpinBox {
                background: #21262d; border: 1px solid #30363d;
                border-radius: 4px; padding: 3px 8px; color: #e6edf3;
            }
            QComboBox::drop-down   { border: none; }
            QLabel                 { color: #e6edf3; }
            QPushButton {
                background: #21262d; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 6px; padding: 4px 12px;
            }
            QPushButton:hover      { background: #30363d; }
        """)


# ── エントリーポイント ────────────────────────────────
def main():
    _env = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False)
                        else os.path.normpath(os.path.join(os.path.dirname(__file__), '..')), '.env')
    load_dotenv(_env)

    app = QApplication(sys.argv)
    app.setApplicationName('OHP Sync Manager')
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
