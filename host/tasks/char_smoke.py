"""单角色冒烟：在新宿主里加载真实角色类，执行底座动作。

用法：
  python -m host.tasks.char_smoke --kl-working I:\\KL\\data\\apps\\kl\\working --char Denia
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from host.compat.stub_task import StubTask
from host.device import Input
from host.scheduler import EventLog

COMPAT = Path(__file__).resolve().parents[1] / "compat"


@dataclass
class SmokeResult:
    char: str
    events: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def load_char_class(kl_working: str | Path, name: str):
    working = str(Path(kl_working))
    sys.path.insert(0, working)
    sys.path.insert(0, str(COMPAT))     # 让 `import kl` 命中垫片而不是旧框架
    module = importlib.import_module(f"src.char.{name}")
    return getattr(module, name)


def run_smoke(kl_working: str | Path, char_name: str, index: int = 0,
              log_path: str | Path | None = None) -> SmokeResult:
    result = SmokeResult(char=char_name)
    log_path = Path(log_path or Path("logs") / f"smoke-{char_name}-{int(time.time())}.jsonl")
    logger = EventLog(log_path)
    inp = Input(dry_run=True)
    task = StubTask(inp, logger)

    try:
        cls = load_char_class(kl_working, char_name)
        char = cls(task, index)
        task.chars = [char]

        char.send_resonance_key()
        char.send_echo_key()
        char.send_liberation_key()
        char.click()
        char.continues_normal_attack(0.12, interval=0.03)
        char.record_resonance_use()
        char.record_liberation_use()
        char.record_echo_use()
        elapsed = char.time_elapsed_accounting_for_freeze(time.time() - 0.05)
        logger.write({"e": "smoke_ok", "char": char.name,
                      "elapsed": round(elapsed, 3),
                      "events": len(inp.events)})
    except Exception as exc:  # 原型阶段把失败原因原样带回
        result.errors.append(f"{type(exc).__name__}: {exc}")
        logger.write({"e": "smoke_fail", "error": result.errors[-1]})
    finally:
        logger.close()

    result.events = inp.events
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kl-working", required=True)
    parser.add_argument("--char", default="Denia")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--log", default=None)
    args = parser.parse_args(argv)

    result = run_smoke(args.kl_working, args.char, args.index, args.log)
    print(f"char={result.char} events={len(result.events)} errors={result.errors}")
    for event in result.events:
        print(f"  {event['type']}: {event.get('key', '')}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
