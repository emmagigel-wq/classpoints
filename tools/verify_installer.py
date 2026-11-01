#!/usr/bin/env python3.14
"""字节码级校验：installer 包（setup.exe 的内层安装器）与 setup 入口。

用法（必须用 3.14 跑，原包是 3.14.8 编译的）：
    PY=/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14
    $PY tools/verify_installer.py --all                  # 全部模块概览
    $PY tools/verify_installer.py installer.winsys       # 单个模块
    $PY tools/verify_installer.py installer.main -v      # 打印指令差异

判定口径与 tools/verify.py 完全一致（直接复用其 check_module，
只把"原始 pyc / 重建源码"的路径映射换成 setup.exe 那一套）：
- 结构一致（必需）：qualname 集合相同 + 指令序列（opname+argval）逐条相同，
  co_names / co_varnames / 参数个数 / flags 一致；
- 字节一致（加分）：co_code 完全相同。

原始 pyc 位于 recover/setup.exe_extracted/：
    PYZ.pyz_extracted/installer/**.pyc   ← installer 包（15 个模块）
    main.pyc                             ← setup.exe 顶层入口（模块名 "main"）
重建源码位于 classpoints-py/：
    installer/**.py                      ← 安装器包
    安装程序.py                           ← setup 入口
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify  # noqa: E402

SETUP_ROOT = os.path.join(verify.ROOT, 'recover', 'setup.exe_extracted')
SRC_ROOT = verify.SRC_ROOT


def installer_module_paths(mod: str) -> tuple[str, str]:
    """setup.exe 侧的路径映射：main → 安装程序.py，installer.* → installer/*.py。"""
    if mod == 'main':
        # setup.exe 的顶层入口与 installer/main.py 字节逐字相同（spec 直接用它当 entry）
        return (os.path.join(SETUP_ROOT, 'main.pyc'),
                os.path.join(SRC_ROOT, 'installer', 'main.py'))
    rel = mod.replace('.', os.sep)
    return (os.path.join(SETUP_ROOT, 'PYZ.pyz_extracted', rel + '.pyc'),
            os.path.join(SRC_ROOT, rel + '.py'))


# verify.check_module 在模块级按名字查 module_paths，替换后即可复用其全部判定逻辑。
verify.module_paths = installer_module_paths

ALL_MODULES = [
    'main',
    'installer.__init__',
    'installer.__main__',
    'installer.constants',
    'installer.bundle',
    'installer.build_payload',
    'installer.winsys',
    'installer.win_shortcut',
    'installer.install_core',
    'installer.uninstaller',
    'installer.main',
    'installer.ui.__init__',
    'installer.ui.style',
    'installer.ui.worker',
    'installer.ui.wizard',
    'installer.ui.pages',
]


def main() -> int:
    verbose = '-v' in sys.argv
    args = [a for a in sys.argv[1:] if a != '-v']
    mods = ALL_MODULES if (not args or args == ['--all']) else args
    ok, rows = 0, []
    for mod in mods:
        state, msgs, stat = verify.check_module(mod, verbose)
        if state == 'ok':
            ok += 1
            mark = '字节一致' if stat.get('bytes') == stat.get('code') else '结构一致'
            print(f'  ✓ {mod:<38} {stat.get("code", 0):>4} code objects  {mark}')
        else:
            print(f'\n✗ {mod}  [{state}]')
            for m in msgs[:14]:
                print('   ', m)
    print(f'== 通过 {ok} / {len(mods)} ==')
    return 0 if ok == len(mods) else 1


if __name__ == '__main__':
    raise SystemExit(main())
