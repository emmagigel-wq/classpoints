#!/usr/bin/env python3.14
"""紧凑反汇编查看器：从原始 pyc 直接渲染某模块/某函数的指令流。

比 recover/export/dis/*.txt 更好用：可直接按 qualname 过滤、可选只显示某函数、
跳转目标显示为 L 标签、常量内联。用法：

    PY=/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14
    $PY tools/peek.py app.ui.history_view            # 模块级 + 函数索引
    $PY tools/peek.py app.ui.history_view _build     # 只看名字含 _build 的函数
    $PY tools/peek.py app.ui.history_view -s         # 连字符串常量表一起打印
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


def load(mod: str) -> CodeType:
    if mod == 'entry_main':
        p = os.path.join(ROOT, 'recover', 'app.exe_extracted', '班级积分系统.pyc')
    else:
        p = os.path.join(PYC_ROOT, mod.replace('.', os.sep) + '.pyc')
    with open(p, 'rb') as fh:
        return marshal.loads(fh.read()[16:])


def walk(code: CodeType):
    yield code
    for c in code.co_consts:
        if isinstance(c, CodeType):
            yield from walk(c)


def render(code: CodeType) -> str:
    lines = [f'# {code.co_qualname}  args={code.co_argcount}/{code.co_kwonlyargcount} '
             f'kwonly={code.co_kwonly_names if hasattr(code,"co_kwonly_names") else ""} '
             f'flags={code.co_flags:#x} varnames={code.co_varnames} freevars={code.co_freevars}']
    ins = list(dis.get_instructions(code))
    pos = {i.offset: k for k, i in enumerate(ins)}
    last_line = None
    for i in ins:
        ln = i.positions.lineno if i.positions else None
        if ln != last_line:
            lines.append(f'  L{ln}:')
            last_line = ln
        arg = i.argval
        if isinstance(arg, CodeType):
            arg = f'<code {arg.co_qualname}>'
        elif isinstance(arg, int) and i.opcode in dis.hasjrel + dis.hasjabs:
            arg = f'-> #{pos.get(arg, arg)}'
        lines.append(f'    {i.opname:<26} {arg!r}'[:150])
    return '\n'.join(lines)


def main(argv: list[str]) -> int:
    mod = argv[0]
    needle = next((a for a in argv[1:] if not a.startswith('-')), None)
    show_str = '-s' in argv
    code = load(mod)
    if show_str:
        print('--- module constants ---')
        for k, c in enumerate(code.co_consts):
            if not isinstance(c, CodeType):
                print(f'  [{k}] {c!r}')
        print()
    allc = list(walk(code))
    if needle is None:
        print(f'# {len(allc)} code objects in {mod}')
        for c in allc:
            print(f'  {c.co_qualname:<60} line {c.co_firstlineno:<5} args {c.co_argcount}')
        print('\n--- module level ---')
        print(render(code))
        return 0
    for c in allc:
        if needle in c.co_qualname:
            print(render(c))
            print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
