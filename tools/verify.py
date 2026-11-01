#!/usr/bin/env python3.14
"""字节码级校验：把重建出来的 .py 与原始 .pyc 逐函数比对。

用法（必须用 3.14 跑，原包是 3.14.8 编译的）：
    PY=/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14
    $PY tools/verify.py --all             # 全部模块概览
    $PY tools/verify.py app.io.backup     # 单个模块
    $PY tools/verify.py app.ui.main_window -v   # 打印指令差异（前后各若干行）

两级判定
--------
1. 结构一致（必需）：同一 qualname 的 code object 集合相同，且指令序列
   （opname + 解析后的 argval）逐条相同；co_names / co_varnames / 参数个数一致。
   这等于「逻辑、常量、调用顺序、分支结构都一样」。
2. 字节一致（加分）：co_code 完全相同（连排版差异都不影响它，但常量索引、
   局部变量顺序等细微差别会打破）。结构一致已足够证明语义还原正确，
   字节一致只是额外确认。

原始 pyc 来自 recover/app.exe_extracted/PYZ.pyz_extracted/，重建源码在
classpoints-py/。两者都是本工作包的产物，不依赖任何外部工具。
"""
from __future__ import annotations

import dis
import io
import marshal
import os
import sys
from types import CodeType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYC_ROOT = os.path.join(ROOT, 'recover', 'app.exe_extracted', 'PYZ.pyz_extracted')
SRC_ROOT = os.path.join(ROOT, 'classpoints-py')
ENTRY_PYC = os.path.join(ROOT, 'recover', 'app.exe_extracted', '班级积分系统.pyc')
ENTRY_SRC = os.path.join(SRC_ROOT, '班级积分系统.py')


def module_paths(mod: str) -> tuple[str, str]:
    if mod == 'entry_main':
        return ENTRY_PYC, ENTRY_SRC
    rel = mod.replace('.', os.sep)
    return os.path.join(PYC_ROOT, rel + '.pyc'), os.path.join(SRC_ROOT, rel + '.py')


def load_pyc(path: str) -> CodeType:
    with open(path, 'rb') as fh:
        return marshal.loads(fh.read()[16:])


def flatten(code: CodeType, out: dict[str, CodeType] | None = None) -> dict[str, CodeType]:
    """qualname -> code，含嵌套；重名加 #n 后缀。"""
    out = {} if out is None else out
    base = code.co_qualname
    key, n = base, 0
    while key in out:
        n += 1
        key = f'{base}#{n}'
    out[key] = code
    for const in code.co_consts:
        if isinstance(const, CodeType):
            flatten(const, out)
    return out


def norm_consts(code: CodeType):
    """常量表；剔除 __firstlineno__ 那个纯行号常量（随排版变化，无语义）。"""
    out = []
    for c in code.co_consts:
        if isinstance(c, CodeType):
            out.append(('CODE', c.co_qualname))
        elif isinstance(c, int) and not isinstance(c, bool) and c == code.co_firstlineno:
            continue
        else:
            out.append(c)
    return out


def instrs(code: CodeType):
    """(opname, argval) 序列；argval 只取标量，便于比对。

    剔除纯排版产物：CACHE/RESUME，类体里 `LOAD_SMALL_INT <行号>` +
    `STORE_NAME '__firstlineno__'`；跳转类指令的绝对偏移换成「相对第几条
    指令」，这样代码长度变化不会造成假差异。
    """
    raw = [ins for ins in dis.get_instructions(code) if ins.opname not in ('CACHE', 'RESUME')]
    # 先算出保留下来的指令及其序号，供跳转目标换算
    keep: list = []
    for idx, ins in enumerate(raw):
        nxt = raw[idx + 1] if idx + 1 < len(raw) else None
        if (ins.opname in ('LOAD_SMALL_INT', 'LOAD_CONST') and nxt is not None
                and nxt.opname == 'STORE_NAME' and nxt.argval == '__firstlineno__'):
            continue
        keep.append(ins)
    pos = {ins.offset: i for i, ins in enumerate(keep)}
    seq = []
    for ins in keep:
        if ins.opcode in dis.hasjrel or ins.opcode in dis.hasjabs:
            target = ins.argval if isinstance(ins.argval, int) else None
            arg = ('JUMP_TO', pos.get(target, f'?{target}'))
        else:
            arg = ins.argval
            if isinstance(arg, CodeType):
                arg = ('CODE', arg.co_qualname)
            elif isinstance(arg, (str, int, float, bool, type(None), tuple, frozenset, bytes)):
                pass
            else:
                arg = repr(arg)
        seq.append((ins.opname, arg))
    return seq


def first_diff(a, b) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f'第 {i} 条指令: {x} vs {y}'
    return f'长度 {len(a)} vs {len(b)}'


def disasm(code: CodeType) -> str:
    buf = io.StringIO()
    dis.dis(code, file=buf, depth=0)
    return buf.getvalue()


def cmp_code(want: CodeType, got: CodeType, verbose: bool):
    problems, byte_same = [], want.co_code == got.co_code
    wi, gi = instrs(want), instrs(got)
    if wi != gi:
        problems.append('指令序列不同: ' + first_diff(wi, gi))
        if verbose:
            import difflib
            wd = [f'{o} {a!r}' for o, a in wi]
            gd = [f'{o} {a!r}' for o, a in gi]
            problems.extend(list(difflib.unified_diff(wd, gd, 'original', 'rebuilt', lineterm='', n=6))[:140])
    for attr in ('co_argcount', 'co_kwonlyargcount', 'co_posonlyargcount', 'co_flags'):
        w, g = getattr(want, attr), getattr(got, attr)
        if w != g:
            problems.append(f'{attr}: {w} vs {g}')
    for attr in ('co_names', 'co_varnames', 'co_freevars', 'co_cellvars'):
        w, g = getattr(want, attr), getattr(got, attr)
        if w != g:
            problems.append(f'{attr}: {w} vs {g}')
    wc, gc = norm_consts(want), norm_consts(got)
    if wc != gc:
        if sorted(map(repr, wc)) == sorted(map(repr, gc)):
            problems.append('co_consts 内容相同但顺序不同（不影响语义，可忽略）')
        else:
            for i, (a, b) in enumerate(zip(wc, gc)):
                if a != b:
                    problems.append(f'co_consts[{i}]: {a!r} vs {b!r}')
                    break
            else:
                longer = wc[len(gc):] if len(wc) > len(gc) else gc[len(wc):]
                problems.append(f'co_consts 长度 {len(wc)} vs {len(gc)}，多出 {longer!r}')
    return problems, byte_same


def check_module(mod: str, verbose: bool = False):
    """返回 (状态, 说明列表, 统计dict)。状态: ok / diff / missing / syntax。"""
    pyc, src = module_paths(mod)
    if not os.path.exists(pyc):
        return 'missing-pyc', [f'找不到原始 pyc: {pyc}'], {}
    if not os.path.exists(src):
        return 'missing-src', [f'未重建: {src}'], {}
    want = flatten(load_pyc(pyc))
    try:
        got = flatten(compile(open(src, encoding='utf-8').read(),
                              pyc.replace('.pyc', '.py'), 'exec', dont_inherit=True))
    except SyntaxError as exc:
        return 'syntax', [f'语法错误: {exc}'], {}
    msgs = []
    missing = [k for k in want if k not in got]
    extra = [k for k in got if k not in want]
    if missing:
        msgs.append(f'缺少代码对象 {len(missing)} 个: {missing[:10]}')
    if extra:
        msgs.append(f'多出代码对象 {len(extra)} 个: {extra[:10]}')
    bad, byte_bad = 0, 0
    for key in want:
        if key not in got:
            continue
        probs, byte_same = cmp_code(want[key], got[key], verbose)
        hard = [p for p in probs if not p.startswith('co_consts 内容相同但顺序不同')]
        if not byte_same:
            byte_bad += 1
        if hard:
            bad += 1
            msgs.append(f'[{key}]\n      ' + '\n      '.join(str(p) for p in hard[:6]))
    stats = {'code': len(want), 'bad': bad, 'bytes': len(want) - byte_bad}
    status = 'ok' if (not missing and not extra and bad == 0) else 'diff'
    return status, msgs, stats


def all_modules() -> list[str]:
    d = os.path.join(ROOT, 'recover', 'export', 'skeleton')
    return [f[:-4] for f in sorted(os.listdir(d))]


def main(argv: list[str]) -> int:
    verbose = '-v' in argv
    args = [a for a in argv if not a.startswith('-')]
    mods = all_modules() if (not args or '--all' in argv) else args
    ok = []
    bad = []
    for mod in mods:
        status, msgs, stats = check_module(mod, verbose)
        if status == 'ok':
            ok.append((mod, stats))
        else:
            bad.append((mod, status, msgs))
    print(f'== 通过 {len(ok)} / {len(mods)} ==')
    for mod, stats in ok:
        mark = '字节一致' if stats.get('bytes') == stats.get('code') else '结构一致'
        print(f'  ✓ {mod:<40} {stats.get("code", 0):>4} code objects  {mark}')
    for mod, status, msgs in bad:
        print(f'\n✗ {mod}  [{status}]')
        for m in msgs[:14]:
            print('   ', m)
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
