"""内置自测的执行器。

**这一层只做三件事**：按顺序跑检查、统一兜异常、把结果整理成两种形态。

- 命令行（`班级积分系统.py --selftest`）要的是**人能读的清单** → `summary()` / `lines()`
- 界面「帮助 → 自检」要的是**能画出来的数据** → `to_dict()`

检查本身的逻辑都在 `checks.py`，这里不认识任何一条检查的业务含义。
一条检查抛异常不该让整轮自测崩掉——那是自测工具最没用的失败方式：
用户看到的应该是「第 N 条炸了，错误是 XXX」，而不是一个空白窗口。
"""

from __future__ import annotations

import platform
import sys
import time
import traceback

from app.selftest.checks import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    CheckDef,
    CheckResult,
    iter_checks,
)

#: 检查抛异常时，错误文本最多保留这么多个字符——
#: traceback 末行偶尔会很长，整段塞进界面会把对话框撑爆。
ERROR_TAIL_CHARS = 600


class SelftestReport:
    """一轮自测的完整结果。"""

    def __init__(self, results: list[CheckResult], duration_ms: float = 0.0):
        self.results = results
        self.duration_ms = duration_ms
        self.finished_at = int(time.time() * 1000)

    @property
    def items(self) -> list[CheckResult]:
        """逐条结果。

        名字叫 items 是为了对上 `app/main.py` 的 `--selftest` 分支
        （那里遍历 `report.items` 逐条打 [PASS]/[FAIL] 行）与界面调用习惯；
        `results` 是同一个列表的别名，两个都留着以免调用方记错。
        """
        return self.results

    @property
    def total(self) -> int:
        return len(self.results)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def passed(self) -> int:
        return self.count(PASS)

    @property
    def failed(self) -> int:
        return self.count(FAIL)

    @property
    def warned(self) -> int:
        return self.count(WARN)

    @property
    def skipped(self) -> int:
        return self.count(SKIP)

    @property
    def ok(self) -> bool:
        """没有失败项就算通过；警告与跳过不判负。

        「跳过」是诚实的降级（例如命令行环境没有 PyQt5 可用于动画检查），
        把它算成失败只会让用户白白看到红叉。
        """
        return self.failed == 0

    @property
    def headline(self) -> str:
        if self.failed:
            return f'发现 {self.failed} 项问题'
        if self.warned:
            return f'全部检查完成，有 {self.warned} 项提醒'
        return '所有检查通过'

    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == FAIL]

    def problems(self) -> list[CheckResult]:
        """需要用户看的东西：失败 + 警告。"""
        return [r for r in self.results if r.status in (FAIL, WARN)]

    def lines(self) -> list[str]:
        out = [f'班级积分系统 自检 —— {self.headline}']
        out.append(
            f'（共 {self.total} 项：通过 {self.passed} / 失败 {self.failed}'
            f' / 提醒 {self.warned} / 跳过 {self.skipped}，耗时 {self.duration_ms:.0f} ms）'
        )
        out.append('')
        for i, r in enumerate(self.results, 1):
            out.append(f'{i:2d}. {r.line()}')
            if r.advice and r.status in (FAIL, WARN):
                out.append(f'    建议：{r.advice}')
        deep = self.problems()
        if deep:
            out.append('')
            out.append('需要处理：')
            for r in deep:
                out.append(f'  · {r.name}：{r.detail}')
                if r.advice:
                    out.append(f'    → {r.advice}')
        return out

    def summary(self) -> str:
        """人类可读的一整段文本——命令行直接 print 这个。"""
        tail = '' if self.ok else '\n\n自检未通过：请把上面「需要处理」的内容发给管理员。'
        return '\n'.join(self.lines()) + tail

    def to_dict(self) -> dict:
        """结构化结果，供界面渲染（键名给界面用，故为驼峰）。"""
        return {
            'ok': self.ok,
            'headline': self.headline,
            'total': self.total,
            'passed': self.passed,
            'failed': self.failed,
            'warned': self.warned,
            'skipped': self.skipped,
            'durationMs': round(self.duration_ms, 1),
            'finishedAt': self.finished_at,
            'environment': environment_info(),
            'checks': [r.to_dict() for r in self.results],
        }


def environment_info() -> dict:
    """自检报告要带的现场信息。

    用户报障时说「打不开」，这几行能省掉三轮来回：哪个版本、哪个系统、
    是不是免安装包里跑的（`frozen`）。
    """
    try:
        from app.version import __version__
    except Exception:
        __version__ = 'unknown'

    frozen = bool(getattr(sys, 'frozen', False))

    return {
        'version': __version__,
        'python': platform.python_version(),
        'platform': platform.platform(),
        'executable': sys.executable,
        'frozen': frozen,
        'packaged': frozen,
    }


def _run_one(spec: CheckDef) -> CheckResult:
    """跑一条检查；任何异常都转成一条可读的失败结果。"""
    started = time.perf_counter()
    try:
        result = spec.func()
    except Exception as exc:
        tb = traceback.format_exc()
        detail = f'检查自身崩了：{type(exc).__name__}: {exc}'
        tail = tb.strip().splitlines()[-1] if tb.strip() else ''
        if tail and tail not in detail:
            detail += f'（{tail}）'
        result = CheckResult(
            key=spec.key,
            name=spec.name,
            status=FAIL,
            detail=detail[:ERROR_TAIL_CHARS],
            advice='这是程序缺陷，请把本行连同下方「需要处理」一并反馈。',
        )
    finally:
        result.duration_ms = (time.perf_counter() - started) * 1000.0
        if not isinstance(result, CheckResult):
            result = CheckResult(
                spec.key,
                spec.name,
                FAIL,
                f'检查返回了非法结果：{type(result).__name__}',
                '这是程序缺陷，请反馈。',
                result.duration_ms if hasattr(result, 'duration_ms') else 0.0,
            )
    result.key = spec.key
    result.name = spec.name
    return result


def run_all(only: list[str] | None = None) -> SelftestReport:
    """跑全部检查（或 only 里指定的那几条 key），返回结果对象。

    约定：这个函数**不打印**——要打印的调用方调 `report.summary()`。
    """
    specs = iter_checks()
    if only:
        wanted = {k for k in only}
        specs = [s for s in specs if s.key in wanted]
    started = time.perf_counter()
    results = [_run_one(spec) for spec in specs]
    return SelftestReport(results, (time.perf_counter() - started) * 1000.0)


def run_one(key: str) -> CheckResult | None:
    """只跑一条（界面里「重测这一项」用）。key 不认识时返回 None。"""
    for spec in iter_checks():
        if spec.key == key:
            return _run_one(spec)
    return None


def summary_text() -> str:
    """便捷入口：跑全量并直接给文本，给 `--selftest` 用。"""
    return run_all().summary()


def main(argv: list[str] | None = None) -> int:
    """`python -m app.selftest` 与 `班级积分系统.py --selftest` 都走这里。

    退出码：0 全过（警告也算过），1 有失败——方便批处理脚本判断。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog='selftest',
        description='班级积分系统 内置自检（纯本地，不联网，不动你的真实数据）',
    )
    parser.add_argument('--only', default='', help='只跑指定项，逗号分隔')
    parser.add_argument('--json', action='store_true', help='输出 JSON 而不是文本清单')
    args = parser.parse_args(argv)

    only = [x.strip() for x in args.only.split(',') if x.strip()]
    report = run_all(only or None)

    if args.json:
        import json
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.summary())
    return 0 if report.ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
