"""在内存里留最近几百条日志, 给详细面板的实时日志用。不依赖 Qt。

框架所有日志都走 logging.getLogger('ok'), 消息是「来源:内容」(kl Logger 拼的)。
这里挂一个 INFO 级的 handler, 按来源拆开存, 面板每秒取一次新增的。
"""
import logging
import threading
from collections import deque

CAPACITY = 500
SOURCE_MAX = 40


def split_source(message):
    """「Hsin:L3心2A1: ...」→ ('Hsin', 'L3心2A1: ...'); 前缀不像来源名时来源为空。"""
    source, sep, text = message.partition(':')
    if sep and source and len(source) <= SOURCE_MAX and ' ' not in source:
        return source, text.strip()
    return '', message


class RecentLogs(logging.Handler):
    def __init__(self, capacity=CAPACITY, level=logging.INFO):
        super().__init__(level)
        self.records = deque(maxlen=capacity)
        self.seq = 0
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return
        source, text = split_source(message)
        with self._lock:
            self.seq += 1
            self.records.append((self.seq, record.created, record.levelno, source, text))

    def since(self, seq):
        """序号大于 seq 的记录: [(序号, 时刻, 级别, 来源, 内容)]。"""
        with self._lock:
            return [r for r in self.records if r[0] > seq]


def install(logger_name='ok'):
    """挂上 (只挂一次) 并返回这个 handler。"""
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers:
        if isinstance(handler, RecentLogs):
            return handler
    handler = RecentLogs()
    logger.addHandler(handler)
    return handler
