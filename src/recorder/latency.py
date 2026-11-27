"""从跑轴记录的事件量「按下 → 程序在画面上认出变化」要多久。不依赖 Qt。

tools/axis_latency.py (命令行看最近几局) 和详细面板 (看正在打的这局) 都用这里。

量两种有明确画面反馈的动作:
- 切人: 按下队伍位按键 (1/2/3) → 队伍栏认出换了人 (field 事件里 i 变成目标位)
- 大招: 按下 R → 队伍 HUD 消失 (field 事件 team 变成 false, 进了大招演出)

每次动作给两个数:
- 上界: 第一次按下 → 第一次认出新状态的那次读屏。包含游戏响应、出画、截图、识别和读屏间隔
- 下界: 第一次按下 → 最后一次还认出旧状态的那次读屏。真实延迟落在上下界之间
还统计每次动作实际按了几下 (第一次按下到认出之间的重发次数)。
"""

# 按下之后这么久还没认出来就不算这一次 (中途被打断、切人失败等)
MAX_LATENCY = 1.5


def analyse(rows):
    """返回 {'switch': [...], 'lib': [...]}, 每项 (上界, 下界, 按了几下), 单位秒。"""
    out = {'switch': [], 'lib': []}
    pending = {}  # 动作名 -> [第一次按下时刻, 按了几下, 目标]
    field = None  # (team, i)
    for r in sorted((r for r in rows if r.get('e') in ('in', 'field')), key=lambda r: r['t']):
        t = r['t']
        # si=1 是前台输入时 SendInput 那一层又记的一遍 (同一下按键), 只认交互方式那一层
        if r['e'] == 'in' and r.get('a') in ('key', 'kd') and not r.get('si'):
            key = str(r.get('k'))
            if key in ('1', '2', '3'):
                target = int(key) - 1
                if field is not None and field[0] and field[1] == target:
                    continue  # 已经是这个人, 补按不算新动作
                p = pending.get('switch')
                if p is None or p[2] != target or t - p[0] > MAX_LATENCY:
                    pending['switch'] = [t, 1, target]
                else:
                    p[1] += 1
            elif key == 'r':
                if field is not None and not field[0]:
                    continue  # HUD 已经不在, 演出里的补按不算
                p = pending.get('lib')
                if p is None or t - p[0] > MAX_LATENCY:
                    pending['lib'] = [t, 1, None]
                else:
                    p[1] += 1
        elif r['e'] == 'field':
            new = (bool(r.get('team')), int(r.get('i', -1)))
            prev_seen = r.get('prev_seen')
            p = pending.get('switch')
            if p and new[0] and new[1] == p[2]:
                if t - p[0] <= MAX_LATENCY:
                    low = max(0.0, prev_seen - p[0]) if prev_seen is not None else None
                    out['switch'].append((t - p[0], low, p[1]))
                pending.pop('switch', None)
            p = pending.get('lib')
            if p and field is not None and field[0] and not new[0]:
                if t - p[0] <= MAX_LATENCY:
                    low = max(0.0, prev_seen - p[0]) if prev_seen is not None else None
                    out['lib'].append((t - p[0], low, p[1]))
                pending.pop('lib', None)
            field = new
    return out


def pct(values, q):
    values = sorted(values)
    if not values:
        return float('nan')
    return values[min(len(values) - 1, int(len(values) * q))]


def median_ms(samples):
    """上界的中位数 (ms), 没有样本返回 None。"""
    if not samples:
        return None
    return pct([s[0] * 1000 for s in samples], .5)
