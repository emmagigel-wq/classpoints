"""自检并补齐 .ps1 的 UTF-8 BOM（README 原话：'.ps1 必须 UTF-8 带 BOM'）。

用法（classpoints-py/ 下）：
    python _tools\\ensure_bom.py            # 检查，缺 BOM 的列出来
    python _tools\\ensure_bom.py --fix      # 原地补 BOM
同时检查 .bat/.cmd 必须纯 ASCII（另一条踩坑规则：'.bat 只放 ASCII'）。
退出码：0=全部合规，1=有违规（--fix 后仍有）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    fix = '--fix' in sys.argv
    bad_bom, bad_ascii = [], []
    for p in sorted(ROOT.rglob('*.ps1')):
        raw = p.read_bytes()
        if not raw.startswith(b'\xef\xbb\xbf'):
            bad_bom.append(p)
            if fix:
                p.write_bytes(b'\xef\xbb\xbf' + raw)
    for p in list(ROOT.rglob('*.bat')) + list(ROOT.rglob('*.cmd')):
        raw = p.read_bytes()
        try:
            raw.decode('ascii')
        except UnicodeDecodeError:
            bad_ascii.append(p)
    for p in bad_bom:
        print(('[fix] 已补 BOM: ' if fix else '[检查] .ps1 缺 UTF-8 BOM: ') + str(p))
    for p in bad_ascii:
        print('[违规] .bat/.cmd 含非 ASCII 字符: ' + str(p))
    if not bad_bom and not bad_ascii:
        print('[ok] packaging 脚本编码全部合规')
        return 0
    return 0 if fix and not bad_ascii else 1


if __name__ == '__main__':
    raise SystemExit(main())
