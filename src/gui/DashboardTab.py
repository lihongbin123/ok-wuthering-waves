"""详细面板: 打的时候想细看就来这里 —— 当前队伍谁在场、场上时间线、延迟和每圈用时、实时日志。

数据全是现成的:
- 跑轴记录器正在写的那局 (logs/axis_runs/*.jsonl), 用 src.recorder.live 边追读边算, 和跑轴复盘页同一套算法
- 计时精度: src.timer_precision 最近一次实测
- 日志: src.log_feed 在内存里留的最近几百条

和主页一样是固定的深色, 不跟浅色主题走。
"""
import html
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from kl import Logger, og
from kl.ui.qt.kit.theme import FONT_FAMILY, MONO_FONT_FAMILY
from src.gui._home_compat import ACCENT, Avatar

logger = Logger.get_logger(__name__)

BG = '#0E1116'
PANEL = '#151A21'
PANEL_2 = '#1B222B'
BORDER = '#232B36'
TEXT = '#E6EDF3'
SUB = '#8B98A8'
DIM = '#6B7787'
WARN = '#E3B341'
DANGER = '#F87171'
SLOT_COLORS = ('#F59E6B', '#9B8CFF', '#4ADE80')
HIDDEN_COLOR = '#3A4250'

TIMELINE_WINDOW = 120.0
LAP_ROWS = 6
LOG_LINES = 300
LOG_MAX_CHARS = 160  # 轴的整圈时间线一条能上千字, 面板里只露个头, 全文去日志文件或跑轴复盘看
REFRESH_MS = 1000

# 记录结束原因里那几个英文的 (战斗任务自己报的脱战原因), 面板上换成中文
END_REASONS = {
    'auto combat disabled': '自动战斗被停了',
    'auto combat restarted': '自动战斗重新开始',
    'target enemy failed': '锁不到怪了(打完或怪跑了)',
    'end condition reached': '打完了',
    'in_combat False': '脱战了',
    'reset_to_false': '脱战了',
    'on_combat_check failed': '战斗检查没过',
    'opening book': '打开了书/菜单',
    'restart Abyss challenge': '深塔重开',
}

FILTER_ALL, FILTER_WARN, FILTER_TEAM = 'all', 'warn', 'team'


# --- 纯逻辑 (有单元测试) -------------------------------------------------------------

def format_seconds(seconds):
    if seconds is None:
        return '—'
    if seconds < 100:
        return f'{seconds:.1f}s'
    minutes, rest = divmod(int(round(seconds)), 60)
    return f'{minutes}:{rest:02d}'


def format_ms(value):
    return '—' if value is None else f'{value:.0f}' if value >= 10 else f'{value:.1f}'


def slot_color(slot):
    return SLOT_COLORS[slot] if isinstance(slot, int) and 0 <= slot < len(SLOT_COLORS) else HIDDEN_COLOR


def run_headline(is_live, ended_reason, lap_count, duration):
    """详细面板标题和副标题。"""
    if is_live:
        title = f'战斗中 · 第 {lap_count} 圈' if lap_count else '战斗中 · 开场轴'
        return title, f'本局 {format_seconds(duration)}'
    reason = END_REASONS.get(ended_reason, ended_reason) if ended_reason else '没有结束记录'
    return '上一局', f'用时 {format_seconds(duration)} · {reason}'


def log_matches(record, mode, team_sources):
    _, _, level, source, _ = record
    if mode == FILTER_WARN:
        return level >= 30
    if mode == FILTER_TEAM:
        return source in team_sources
    return True


def log_html(record, labels, colors):
    """一行日志: 时刻 来源 内容; 来源是队伍里的人时换成中文名, 按号位上色。"""
    _, created, level, source, text = record
    clock = time.strftime('%H:%M:%S', time.localtime(created))
    color = DANGER if level >= 40 else WARN if level >= 30 else TEXT
    name = labels.get(source, source)
    source_color = colors.get(source, SUB)
    if len(text) > LOG_MAX_CHARS:
        text = text[:LOG_MAX_CHARS] + ' …'
    parts = [f'<span style="color:{DIM}">{clock}</span>']
    if name:
        parts.append(f'<span style="color:{source_color}">{html.escape(name)}</span>')
    parts.append(f'<span style="color:{color}">{html.escape(text)}</span>')
    return ' '.join(parts)


# --- 小部件 ---------------------------------------------------------------------------

def _style(label, size=13, weight=400, color=TEXT, mono=False):
    family = MONO_FONT_FAMILY if mono else FONT_FAMILY
    label.setStyleSheet(f'color: {color}; background: transparent; font-family: {family}; '
                        f'font-size: {size}px; font-weight: {weight};')


def _label(text='', size=13, weight=400, color=TEXT, mono=False):
    label = QLabel(text)
    _style(label, size, weight, color, mono)
    return label


def _panel(title=None):
    frame = QFrame()
    frame.setObjectName('panel')
    frame.setStyleSheet(f'QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}')
    column = QVBoxLayout(frame)
    column.setContentsMargins(14, 12, 14, 14)
    column.setSpacing(8)
    if title:
        column.addWidget(_label(title, 12, 500, SUB))
    return frame, column


class FieldTimeline(QWidget):
    """场上时间线: 每段按号位上色, HUD 不在 (大招演出等) 画灰色; 右边竖线是现在。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self._start, self._end, self._segments, self._live = 0.0, 1.0, [], False

    def set_data(self, start, end, segments, live):
        self._start, self._end, self._segments, self._live = start, max(end, start + 1.0), segments, live
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 0, self.width(), 26)
        path = QPainterPath()
        path.addRoundedRect(bar, 5, 5)
        painter.fillPath(path, QColor(PANEL_2))
        painter.setClipPath(path)
        span = self._end - self._start
        for t0, t1, slot in self._segments:
            x0 = (t0 - self._start) / span * bar.width()
            x1 = (t1 - self._start) / span * bar.width()
            painter.fillRect(QRectF(x0, 0, max(1.0, x1 - x0), bar.height()), QColor(slot_color(slot)))
        painter.setClipping(False)
        if self._live:
            painter.fillRect(QRectF(bar.width() - 2, -1, 2, bar.height() + 2), QColor(TEXT))
        painter.setPen(QColor(DIM))
        font = QFont(painter.font())
        font.setPixelSize(11)
        painter.setFont(font)
        for i in range(5):
            t = self._start + span * i / 4
            x = bar.width() * i / 4
            if i == 0:
                box, align = QRectF(x, 30, 60, 16), Qt.AlignmentFlag.AlignLeft
            elif i == 4:
                box, align = QRectF(x - 60, 30, 60, 16), Qt.AlignmentFlag.AlignRight
            else:
                box, align = QRectF(x - 30, 30, 60, 16), Qt.AlignmentFlag.AlignHCenter
            painter.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, f'{t:.0f}s')


class Bar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fraction, self._color = 0.0, QColor(ACCENT)

    def set_value(self, fraction, color):
        self._fraction, self._color = max(0.0, min(1.0, fraction)), QColor(color)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PANEL_2))
        painter.drawRoundedRect(QRectF(self.rect()), 5, 5)
        if self._fraction > 0:
            painter.setBrush(self._color)
            painter.drawRoundedRect(QRectF(0, 0, self.width() * self._fraction, self.height()), 5, 5)


class StatTile(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('tile')
        self.setStyleSheet(f'QFrame#tile {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}')
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 10, 14, 10)
        column.setSpacing(2)
        column.addWidget(_label(title, 12, 400, SUB))
        row = QHBoxLayout()
        row.setSpacing(3)
        self.value = _label('—', 24, 600, TEXT, mono=True)
        self.unit = _label('', 12, 400, SUB)
        row.addWidget(self.value)
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        column.addLayout(row)
        self.note = _label('', 11, 400, DIM)
        column.addWidget(self.note)

    def set(self, value, unit='', note=''):
        self.value.setText(value)
        self.unit.setText(unit if value != '—' else '')
        self.note.setText(note)


class MemberRow(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('member')
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(10)
        self.avatar = Avatar(32)
        row.addWidget(self.avatar)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.name = _label('', 13, 600)
        self.detail = _label('', 11, 400, DIM)
        text.addWidget(self.name)
        text.addWidget(self.detail)
        row.addLayout(text, 1)
        self.state = _label('', 11, 700)
        row.addWidget(self.state)
        self.set_active(False, TEXT)

    def set_active(self, active, color):
        background = PANEL_2 if active else 'transparent'
        edge = f'border-left: 2px solid {color};' if active else 'border-left: 2px solid transparent;'
        self.setStyleSheet(f'QFrame#member {{ background: {background}; border-radius: 8px; {edge} }}')


# --- 页面 -----------------------------------------------------------------------------

class DashboardTab(QWidget):
    # MainWindow 读这几个: 放在「运行」分组里, 跟在「任务」后面
    add_after_default_tabs = True
    nav_section = 'run'
    icon = 'pulse'

    def __init__(self):
        super().__init__()
        self.setObjectName('coco_dashboard')
        self.name = '详细面板'
        self.executor = None
        self._tail = None
        self._is_live = False
        self._log_seq = 0
        self._log_records = []
        self._log_mode = FILTER_ALL
        self._team_sources = set()
        self._labels = {}
        self._colors = {}
        try:
            from src import log_feed
            self._logs = log_feed.install()
        except Exception as e:
            logger.warning(f'详细面板: 接不上日志 ({e})')
            self._logs = None
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f'QWidget#coco_dashboard {{ background: {BG}; }}')
        self._build()

    # --- 搭界面 ---------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(_label('详细面板', 18, 700))
        self.state_pill = _label('', 12, 700, ACCENT)
        header.addWidget(self.state_pill)
        self.run_name = _label('', 12, 400, DIM, mono=True)
        header.addWidget(self.run_name)
        header.addStretch(1)
        # 首页、跑轴复盘都在顶上标签栏里, 这里不再放跳转按钮
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(self._build_left(), 0)
        body.addLayout(self._build_middle(), 1)
        body.addWidget(self._build_logs(), 0)
        root.addLayout(body, 1)

    def _build_left(self):
        column = QVBoxLayout()
        column.setSpacing(12)
        team, team_column = _panel('当前队伍')
        team.setFixedWidth(250)
        self.team_title = _label('', 17, 700)
        self.team_title.setWordWrap(True)
        team_column.addWidget(self.team_title)
        self.members = [MemberRow() for _ in range(3)]
        for member in self.members:
            team_column.addWidget(member)
        column.addWidget(team)

        tasks, tasks_column = _panel('任务')
        tasks.setFixedWidth(250)
        self.task_rows = {}
        for key, title in (('combat', '自动战斗'), ('farm', '刷4C声骸')):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = _label('●', 10, 400, DIM)
            row.addWidget(dot)
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(_label(title, 13, 600))
            detail = _label('', 11, 400, SUB)
            text.addWidget(detail)
            row.addLayout(text, 1)
            tasks_column.addLayout(row)
            self.task_rows[key] = (dot, detail)
        column.addWidget(tasks)
        column.addStretch(1)
        return column

    def _build_middle(self):
        column = QVBoxLayout()
        column.setSpacing(12)
        status, status_column = _panel()
        title_row = QHBoxLayout()
        self.headline = _label('', 22, 700)
        self.subline = _label('', 12, 400, SUB, mono=True)
        title_row.addWidget(self.headline)
        title_row.addStretch(1)
        title_row.addWidget(self.subline)
        status_column.addLayout(title_row)
        self.status_detail = _label('', 13, 400, SUB)
        status_column.addWidget(self.status_detail)
        status_column.addSpacing(6)
        legend_row = QHBoxLayout()
        legend_row.addWidget(_label('场上时间线', 12, 400, SUB))
        legend_row.addStretch(1)
        self.legend = _label('', 11, 400, SUB)
        legend_row.addWidget(self.legend)
        status_column.addLayout(legend_row)
        self.timeline = FieldTimeline()
        status_column.addWidget(self.timeline)
        column.addWidget(status)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tile_switch = StatTile('切人延迟 中位')
        self.tile_lib = StatTile('大招延迟 中位')
        self.tile_timer = StatTile('计时精度')
        self.tile_time = StatTile('本局用时')
        for tile in (self.tile_switch, self.tile_lib, self.tile_timer, self.tile_time):
            tiles.addWidget(tile, 1)
        column.addLayout(tiles)

        laps, laps_column = _panel()
        laps_header = QHBoxLayout()
        laps_header.addWidget(_label('每圈用时', 12, 500, SUB))
        laps_header.addStretch(1)
        self.laps_note = _label('', 11, 400, DIM)
        laps_header.addWidget(self.laps_note)
        laps_column.addLayout(laps_header)
        self.lap_rows = []
        for _ in range(LAP_ROWS):
            row = QHBoxLayout()
            row.setSpacing(12)
            number = _label('', 12, 400, SUB, mono=True)
            number.setFixedWidth(40)
            bar = Bar()
            value = _label('', 12, 400, TEXT, mono=True)
            value.setFixedWidth(64)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(number)
            row.addWidget(bar, 1)
            row.addWidget(value)
            laps_column.addLayout(row)
            self.lap_rows.append((number, bar, value))
        self.laps_empty = _label('', 12, 400, DIM)
        self.laps_empty.setWordWrap(True)
        laps_column.addWidget(self.laps_empty)
        laps_column.addStretch(1)
        column.addWidget(laps, 1)
        return column

    def _build_logs(self):
        frame = QFrame()
        frame.setObjectName('logs')
        frame.setFixedWidth(330)
        frame.setStyleSheet(f'QFrame#logs {{ background: #0B0E12; border: 1px solid {BORDER}; border-radius: 10px; }}')
        column = QVBoxLayout(frame)
        column.setContentsMargins(12, 10, 12, 12)
        column.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(4)
        header.addWidget(_label('实时日志', 12, 500, SUB))
        header.addStretch(1)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons = {}
        for mode, text in ((FILTER_ALL, '全部'), (FILTER_WARN, '警告'), (FILTER_TEAM, '轴')):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(22)
            button.setStyleSheet(
                f'QPushButton {{ background: transparent; color: {SUB}; border: 0; border-radius: 5px; '
                f'padding: 0 8px; font-family: {FONT_FAMILY}; font-size: 11px; }}'
                f'QPushButton:checked {{ background: {PANEL_2}; color: {TEXT}; }}'
                f'QPushButton:hover {{ color: {TEXT}; }}')
            button.clicked.connect(lambda _=False, m=mode: self._set_log_mode(m))
            self.filter_group.addButton(button)
            self.filter_buttons[mode] = button
            header.addWidget(button)
        self.filter_buttons[FILTER_ALL].setChecked(True)
        column.addLayout(header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_LINES)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setFrameShape(QFrame.Shape.NoFrame)
        self.log_view.setStyleSheet(
            f'QPlainTextEdit {{ background: transparent; color: {TEXT}; border: 0; '
            f'font-family: {MONO_FONT_FAMILY}; font-size: 11px; }}'
            f'QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}'
            f'QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}'
            f'QScrollBar::handle:vertical:hover {{ background: {DIM}; }}'
            f'QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}'
            f'QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}')
        column.addWidget(self.log_view, 1)
        return frame

    # --- 刷新 -----------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()
        self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    @staticmethod
    def _tr(text):
        app = getattr(og, 'app', None)
        return app.tr(text) if app is not None and text else (text or '')

    def _executor(self):
        return self.executor or getattr(og, 'executor', None)

    def refresh(self):
        try:
            self._refresh_run()
        except Exception as e:
            logger.error(f'详细面板刷新出错: {e}')
        try:
            self._refresh_tasks()
            self._refresh_logs()
        except Exception as e:
            logger.error(f'详细面板刷新出错: {e}')

    def _current_path(self):
        from src.recorder import live
        try:
            from src.recorder.AxisRecorder import current_recorder
            recorder = current_recorder()
        except Exception:
            recorder = None
        return live.current_run_path(recorder)

    def _refresh_run(self):
        from src.recorder import live
        path, is_live = self._current_path()
        if path is None:
            self._show_empty()
            return
        if self._tail is None or self._tail.path != path:
            self._tail = live.RunTail(path)
        changed = self._tail.poll()
        if not changed and is_live == self._is_live and self._tail.data() is not None and self.headline.text():
            return
        self._is_live = is_live
        data = self._tail.data()
        members = live.team_members(data)
        self._labels = {cls: self._tr(cls) for _, cls, _ in members}
        self._colors = {cls: slot_color(slot) for slot, cls, _ in members}
        self._team_sources = set(self._labels)
        on_field = live.current_slot(data)
        totals = live.field_time(data)
        self.team_title.setText(' · '.join(self._labels.get(cls, label) for _, cls, label in members) or '—')
        for row, member in zip(self.members, members + [None] * 3):
            row.setVisible(member is not None)
            if member is None:
                continue
            slot, cls, label = member
            name = self._labels.get(cls, label)
            row.avatar.set_char(cls, name)
            row.name.setText(name)
            row.detail.setText(f'{slot + 1} 号位 · 在场 {format_seconds(totals.get(slot, 0.0))}')
            active = slot == on_field
            row.set_active(active, slot_color(slot))
            row.state.setText('在场' if active and is_live else '')
            _style(row.state, 11, 700, slot_color(slot))

        lap_slot, period, laps = live.cycle_laps(data, LAP_ROWS)
        in_progress = laps[-1][0] + 1 if laps else 0  # 正在跑第几圈 (从循环开始算, 开场轴不算)
        title, sub = run_headline(is_live, (data.end or {}).get('reason'), in_progress, data.duration)
        self.headline.setText(title)
        self.subline.setText(sub)
        on_field_name = next((self._labels.get(cls, label) for slot, cls, label in members if slot == on_field), '')
        self.status_detail.setText(f'{on_field_name} 在场' if is_live and on_field_name else
                                   ('' if is_live else '打的时候这里每秒更新'))
        self.state_pill.setText('● 记录中' if is_live else '上一局')
        _style(self.state_pill, 12, 700, ACCENT if is_live else SUB)
        self.run_name.setText(path.replace('\\', '/').rsplit('/', 1)[-1])

        window = min(TIMELINE_WINDOW, max(data.duration, 1.0))
        start, end, segments = live.field_segments(data, window)
        self.timeline.set_data(start, end, segments, is_live)
        self.legend.setText('   '.join(
            f'<span style="color:{slot_color(slot)}">■</span> {html.escape(self._labels.get(cls, label))}'
            for slot, cls, label in members) + f'   <span style="color:{HIDDEN_COLOR}">■</span> 演出/看不到')
        self.legend.setTextFormat(Qt.TextFormat.RichText)

        latency = live.latency_summary(data)
        switch_ms, switch_n = latency['switch']
        lib_ms, lib_n = latency['lib']
        self.tile_switch.set(format_ms(switch_ms), 'ms', f'{switch_n} 次' if switch_n else '还没切过人')
        self.tile_lib.set(format_ms(lib_ms), 'ms', f'{lib_n} 次' if lib_n else '还没放过大招')
        self._refresh_timer_tile()
        self.tile_time.set(format_seconds(data.duration), '', '进行中' if is_live else '已结束')

        if laps:
            longest = max(duration for _, _, duration in laps)
            start_name = next((self._labels.get(cls, label) for slot, cls, label in members if slot == lap_slot), '')
            self.laps_note.setText(f'从 {start_name} 上场算一圈 · 每圈换 {period} 次人')
            self.laps_empty.hide()
        else:
            self.laps_note.setText('')
            self.laps_empty.setText('还没找到重复的循环：至少跑满两整圈后这里会列出每圈用时。')
            self.laps_empty.show()
        latest = laps[-1][0] if laps else None
        for (number, bar, value), lap in zip(self.lap_rows, laps + [None] * LAP_ROWS):
            for widget in (number, bar, value):
                widget.setVisible(lap is not None)
            if lap is None:
                continue
            index, _, duration = lap
            number.setText(f'#{index}')
            bar.set_value(duration / longest if longest else 0, ACCENT if index == latest else '#7A6A3A')
            value.setText(f'{duration:.1f}s')

    def _refresh_timer_tile(self):
        try:
            from src import timer_precision
            measured = timer_precision.last_measurement()
        except Exception:
            measured = None
        if measured is None:
            self.tile_timer.set('—', 'ms', '还没量过')
            return
        value, tag, _ = measured
        self.tile_timer.set(f'{value:.1f}', 'ms', f'wait(2ms) 实测 · {tag}')

    def _show_empty(self):
        self.headline.setText('还没有跑轴记录')
        self.subline.setText('')
        self.status_detail.setText('在任务页打开「跑轴记录」，进一次战斗后这里就有数据')
        self.state_pill.setText('')
        self.run_name.setText('')
        self.team_title.setText('—')
        for row in self.members:
            row.hide()
        self.timeline.set_data(0, 1, [], False)
        for tile in (self.tile_switch, self.tile_lib, self.tile_time):
            tile.set('—')
        self._refresh_timer_tile()
        for row in self.lap_rows:
            for widget in row:
                widget.hide()
        self.laps_note.setText('')
        self.laps_empty.setText('')

    def _refresh_tasks(self):
        executor = self._executor()
        auto = farm = None
        if executor is not None:
            try:
                from src.task.AutoCombatTask import AutoCombatTask
                from src.task.FarmEchoTask import FarmEchoTask
                auto = executor.get_task_by_class(AutoCombatTask)
                farm = executor.get_task_by_class(FarmEchoTask)
            except Exception:
                pass
        paused = getattr(executor, 'paused', True)
        dot, detail = self.task_rows['combat']
        if auto is None:
            self._set_task_row(dot, detail, DIM, '—')
        elif getattr(executor, 'current_task', None) is auto:
            self._set_task_row(dot, detail, ACCENT, '战斗中')
        elif auto.enabled and not paused:
            self._set_task_row(dot, detail, '#4ADE80', '待命 · 进战斗自动开打')
        else:
            self._set_task_row(dot, detail, DIM, '已关闭' if not auto.enabled else '未开始')
        dot, detail = self.task_rows['farm']
        if farm is None:
            self._set_task_row(dot, detail, DIM, '—')
            return
        from kl.ui.qt.tasks.tasks_page import task_status
        status = task_status(farm, executor)
        name = farm.config.get('Weekly Boss Name') if farm.config.get('Teleport to Boss') == 'Weekly Challenge' else ''
        info = getattr(farm, 'info', None) or {}
        count = info.get('Echo Count')
        extra = f' · 获得 {count}' if count not in (None, '') and status else ''
        if status:
            self._set_task_row(dot, detail, ACCENT, f'{status[0]}{extra}')
        else:
            self._set_task_row(dot, detail, DIM, f'待命{" · " + name if name else ""}')

    @staticmethod
    def _set_task_row(dot, detail, color, text):
        dot.setStyleSheet(f'color: {color}; background: transparent; font-size: 10px;')
        detail.setText(text)

    def _refresh_logs(self):
        if self._logs is None:
            return
        new = self._logs.since(self._log_seq)
        if not new:
            return
        self._log_seq = new[-1][0]
        self._log_records = (self._log_records + new)[-LOG_LINES * 3:]
        lines = [log_html(r, self._labels, self._colors) for r in new
                 if log_matches(r, self._log_mode, self._team_sources)]
        if lines:
            self._append_logs(lines)

    def _append_logs(self, lines):
        bar = self.log_view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        for line in lines:
            self.log_view.appendHtml(line)
        if at_bottom:
            bar.setValue(bar.maximum())
            # 一次塞很多行时文档排版是懒的, 滚动范围下一轮事件循环才算全, 再拉一次到底
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _set_log_mode(self, mode):
        self._log_mode = mode
        self.log_view.clear()
        lines = [log_html(r, self._labels, self._colors) for r in self._log_records
                 if log_matches(r, mode, self._team_sources)]
        self._append_logs(lines[-LOG_LINES:])
