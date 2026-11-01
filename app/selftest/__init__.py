"""内置自测：给最终用户（老师）用的「体检」。

**为什么装进程序里**：这个程序要打包成安装包发给老师，目标机器上没有 Python、
没有开发工具，出问题时没人能看日志。所以「哪儿坏了」必须由程序自己说清楚。

三条边界，写在最前面，改动时不要越线：

1. **纯本地**：不联网、不下载、不读注册表、不调外部程序。
2. **零额外依赖**：只用标准库 + 程序本来就带的库。缺 PyQt5 时相关检查
   标「跳过」而不是判失败——命令行环境跑自测不该整片飘红。
3. **绝不动真实数据**：凡是要读写存档的检查，一律把数据目录指到临时目录
   （见 `checks.temp_data_dir`）。写真实目录的只有「数据目录可写」一条，
   而且只写一个探针文件、随即删除。

两种用法：

    # 命令行
    from app.selftest import run_all
    report = run_all()
    print(report.summary())          # 人类可读清单
    data = report.to_dict()          # 给界面渲染的结构化数据

    # 命令行入口
    python -m app.selftest --json
"""

from __future__ import annotations

from app.selftest.checks import (
    FAIL,
    PASS,
    SKIP,
    STATUS_LABELS,
    WARN,
    CheckDef,
    CheckResult,
    iter_checks,
)
from app.selftest.runner import (
    SelftestReport,
    environment_info,
    main,
    run_all,
    run_one,
    summary_text,
)

__all__ = [
    'PASS',
    'FAIL',
    'WARN',
    'SKIP',
    'STATUS_LABELS',
    'CheckDef',
    'CheckResult',
    'SelftestReport',
    'run_all',
    'run_one',
    'summary_text',
    'environment_info',
    'main',
    'iter_checks',
]
