"""任务垫片：用新宿主的 Input/Logger 实现角色代码需要的 task API。

这一层是第 2 档的关键：角色（src/char/*）原样保留，只把它依赖的
task 接口换成本宿主的实现。当前覆盖底座动作，够做单角色冒烟与轴逻辑迁移。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from host.device import Input
from host.scheduler import EventLog

try:
    from host.config import HostConfig
    _HUD_CFG = HostConfig.load()
except Exception:  # noqa: BLE001
    _HUD_CFG = {}


class StubTask:
    def __init__(self, inp: Input, logger: EventLog, config=None,
                 vision=None, screen_provider=None):
        self.input = inp
        self.logger_events = logger
        self.config = config or {}
        self.vision = vision
        self.screen_provider = screen_provider
        self.hud_template = None
        self.skill_state = None       # callable(box_name) -> bool
        self.team_probe = None        # callable() -> bool，HUD 判定在场
        self.cd_state = None          # callable(box_name, index) -> bool
        self.con_value = 0.0
        self._templates: dict[str, str] = {}
        self.template_root = _HUD_CFG.get("hud.template_root")
        self.hud_templates = _HUD_CFG.get("hud.templates", {})
        self.chars = []
        self.in_liberation = False
        self.skip_combat_check = False
        self.has_lavitator = False
        self.key_config = {
            "Resonance Key": "e",
            "Liberation Key": "r",
            "Echo Key": "q",
            "Tool Key": "t",
            "Jump Key": "space",
            "Dodge Key": "lshift",
            "Wheel Key": "tab",
        }
        self._clock_offset = 0.0

    # ---- 基础日志 ----
    def _log(self, level: str, message: str) -> None:
        self.logger_events.write({"e": "log", "lvl": level, "m": str(message)})

    def log_info(self, message, *a, **k): self._log("info", message)
    def log_debug(self, message, *a, **k): self._log("debug", message)
    def log_error(self, message, *a, **k): self._log("error", message)
    def log_warning(self, message, *a, **k): self._log("warning", message)
    def tr(self, text, *a, **k): return text

    # ---- 按键 ----
    def send_key(self, key, interval=-1, down_time=0.01, after_sleep=0, **kwargs):
        self.input.key(str(key), hold_ms=int(max(0, down_time) * 1000))
        self.logger_events.write({"e": "send_key", "key": str(key)})
        if after_sleep:
            time.sleep(float(after_sleep))

    def click(self, interval=-1, down_time=0.01, after_sleep=0, **kwargs):
        self.input.click(kwargs.get("x"), kwargs.get("y"))
        self.logger_events.write({"e": "click"})
        if interval and interval > 0:
            time.sleep(interval)
        if after_sleep:
            time.sleep(float(after_sleep))

    def macro_spam(self, times=3, interval_ms=30, **kwargs):
        """连点桥接：角色起手里补普攻时用。"""
        self.input.macro_spam(int(times), int(interval_ms))
        self.logger_events.write({"e": "macro_spam", "times": int(times),
                                  "interval_ms": int(interval_ms)})

    def mouse_down(self, *a, **k): self.input.mouse_down()
    def mouse_up(self, *a, **k): self.input.mouse_up()

    def get_resonance_key(self): return self.key_config["Resonance Key"]
    def get_liberation_key(self): return self.key_config["Liberation Key"]
    def get_echo_key(self): return self.key_config["Echo Key"]

    # ---- 时间/循环 ----
    def next_frame(self):
        time.sleep(0.001)

    def sleep(self, sec):
        end = time.time() + max(0.0, float(sec))
        while time.time() < end:
            time.sleep(min(0.005, max(0.0, end - time.time())))

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return time.time() - float(start)

    def wait_until(self, condition: Callable[[], bool], time_out=5, post_action=None):
        end = time.time() + time_out
        while time.time() < end:
            if condition():
                return True
            if post_action:
                post_action()
            self.next_frame()
        return False

    def check_f_break(self):  # 物理 F 中断：新宿主暂不实现
        return False

    def check_combat(self):
        return None

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        self.logger_events.write({"e": "freeze", "start": start,
                                  "duration": duration})

    # ---- 视觉（接 host.vision） ----
    def current_frame(self):
        if self.screen_provider is None:
            return None
        return self.screen_provider()

    @property
    def frame(self):
        return self.current_frame()

    def box_of_screen_scaled(self, design_w, design_h, x1, y1, x2, y2,
                             name=None, hcenter=False, **kwargs):
        frame = self.current_frame()
        if hcenter:
            center = (x1 + x2) // 2
            half = (x2 - x1) // 2
            x1, x2 = center - half, center + half
        from host.compat.boxes import Box, scale_region
        if frame is None:
            return Box(0, 0, 1, 1, None, name=name)
        return scale_region(design_w, design_h, frame, x1, y1, x2, y2)

    def draw_boxes(self, *args, **kwargs):
        return None

    def calculate_color_percentage(self, color, box, threshold=None, **kwargs):
        from host.compat.boxes import color_percentage
        if box is None:
            return 0.0
        return color_percentage(color, box, tolerance=threshold or 12)

    def find_one(self, target, threshold=None, region=None, **kwargs):
        if self.vision is None:
            return None
        path = target if isinstance(target, (str, Path)) else getattr(target, "path", None)
        if path is None:
            return None
        frame = self.current_frame()
        if frame is None:
            return None
        raw = getattr(path, "value", None) or str(path)
        name = Path(raw).stem
        if "." in name:  # Labels.qingxiao_e -> qingxiao_e（str 枚举的 str() 带类名）
            name = name.rsplit(".", 1)[-1]
        spec = self.hud_templates.get(name)
        if spec:
            # Labels.qingxiao_e -> 已校准的模板文件 + 区域 + 阈值
            path = Path(self.template_root) / spec["file"]
            region = tuple(spec["region"]) if region is None else region
            threshold = spec["threshold"] if threshold is None else threshold
        elif not Path(str(path)).exists() and self.template_root:
            # 其它 Labels：按名字在模板目录里找 qingxiao_e_<res>_x_y.png
            hits = sorted(Path(self.template_root).glob(f"{name}_*.png"))
            if hits:
                path = hits[0]
            else:
                return None
        return self.vision.match(frame, path, region=region, threshold=threshold)

    def find_e_forte(self):
        return self._match_template(self._templates.get("e_forte"))

    def find_mouse_forte(self):
        return self._match_template(self._templates.get("mouse_forte"))

    def _match_template(self, path):
        if not path:
            return False
        return self.find_one(path) is not None

    def available(self, box, check_color=True, check_cd=True):
        if self.skill_state is not None:
            return bool(self.skill_state(box))
        if check_cd and self.has_cd(box):
            return False
        return True

    def is_con_full(self):
        return self.con_value >= 1.0

    def get_current_con(self):
        return self.con_value

    def in_team(self):
        """当前角色是否在场：优先用 HUD 模板探针，其次旧的单模板，最后兜底 True。"""
        chars = [c for c in (self.chars or []) if c is not None]
        index = next((getattr(c, "index", i) for i, c in enumerate(chars)
                      if getattr(c, "is_current_char", False)), 0)
        count = len(chars) or 1
        if callable(self.team_probe):
            try:
                value = self.team_probe()
            except Exception:
                value = True
            if isinstance(value, tuple) and len(value) == 3:
                ok, cur, num = value
                self.logger_events.write({"e": "in_team", "value": bool(ok),
                                          "current": cur, "count": num})
                return bool(ok), (cur if cur >= 0 else index), num
            self.logger_events.write({"e": "in_team", "value": bool(value)})
            return bool(value), index, count
        if self.vision is not None and self.hud_template:
            frame = self.current_frame()
            if frame is not None:
                return self.vision.match(frame, self.hud_template) is not None, index, count
        return True, index, count

    def box_highlighted(self, box, *a, **k):
        if self.skill_state is not None:
            return bool(self.skill_state(box))
        return False

    def has_cd(self, box, index=None):
        if self.cd_state is not None:
            return bool(self.cd_state(box, index))
        return False

    def screenshot(self, *a, **k): return None

    def switch_next_char(self, char=None, post_action=None, free_intro=False,
                         target_low_con=False):
        """切人桥接：按轮盘键，并把当前角色标记移到队伍中的下一个。"""
        key = self.key_config.get("Wheel Key", "tab")
        self.input.key(key)
        self.logger_events.write({"e": "switch_next_char", "key": key})
        chars = [item for item in (self.chars or []) if item is not None]
        if chars:
            current = char if char in chars else next(
                (item for item in chars if getattr(item, "is_current_char", False)), None)
            if current is not None and len(chars) > 1:
                index = chars.index(current)
                current.is_current_char = False
                chars[(index + 1) % len(chars)].is_current_char = True
        if callable(post_action):
            post_action()
