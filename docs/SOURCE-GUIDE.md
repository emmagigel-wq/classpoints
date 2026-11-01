# 源码介绍 —— 班级积分系统（Python 重写版）

> 版本：**2.13.16**（唯一来源 `app/version.py`）
> 规模：`app/` 77 个模块 25,480 行；`installer/` 14 个模块 2,394 行；入口 118 行。

这是一份「读代码之前先看」的导览：讲清楚这套源码分成哪几层、每层负责什么、
一次「加分」和一次「早读舞台」的数据是怎么流动的，以及安装器与打包链是怎么拼起来的。

---

## 1. 这是什么

`班级积分系统` 原本是一个网页版（HTML + `app.js`，版本 1.2.201）。本工程是它的
**PyQt5 桌面重写版**：功能对齐、视觉按原版 HTML 的 `:root` 令牌与实测渲染值 1:1 复刻，
但数据、状态、延迟动作全部搬进了明确的 Python 分层里。

三个硬约定贯穿全工程：

1. **版本号只有一个来源** —— `app/version.py`。标题栏、导出文件名、备份文件头、
   安装包 manifest 全读它，避免「顶栏一个版本、导出文件另一个版本」。
2. **纯逻辑与界面分离** —— `app/core/` 不 import 任何 Qt，可以脱离界面单测；
   `app/services/` 是唯一把「界面意图」翻译成「core 调用」的地方。
3. **延迟动作统一登记** —— 所有 `QTimer`、闪一下、几秒后回收，都走
   `TimerRegistry`，退出时一次收干净，不留「幽灵分」。

---

## 2. 目录结构

```
classpoints-py/
├── 班级积分系统.py          启动入口（异常兜底 / noconsole 日志 / --selftest）
├── app/
│   ├── main.py              装配层：加载数据 → 建主窗口 → 收退出清理
│   ├── version.py           版本号唯一来源
│   ├── core/                纯逻辑层（14 模块，3,398 行，无 Qt）
│   ├── io/                  导入导出（6 模块，2,393 行）
│   ├── services/            协调层（16 模块，5,290 行）
│   ├── ui/                  界面层（12 模块，6,172 行）
│   │   ├── dialogs/         各类弹窗（13 模块，4,315 行）
│   │   └── widgets/         通用小部件（10 模块，2,612 行）
│   └── selftest/            内置自检（3 模块，1,028 行）
├── installer/               安装程序（14 模块，2,394 行，纯 stdlib + 可选 PyQt5）
│   └── ui/                  四页安装向导
├── packaging/               PyInstaller spec 与 Windows 构建脚本
├── docs/                    BUILD.md 与本文件
└── _tools/ensure_bom.py     .ps1 的 UTF-8 BOM 自检/补齐
```

---

## 3. 分层职责

### 3.1 `app/core/` —— 纯逻辑层

只处理数据与规则，不碰界面、不碰定时器。可以单独 import 做单元测试。

| 模块 | 职责 |
| --- | --- |
| `models.py` | 数据模型：`AppState` / `Student` / `Layout` / `Podium` / `Prize` / `LotteryRecord` 等纯数据类 |
| `state.py` | 状态容器：把 `AppState` 与「变更通知 / 脏标记 / 保存节奏」绑在一起 |
| `storage.py` | 落盘：原子写入、备份轮转、大文件旁路存储 |
| `scoring.py` | 记分 / 撤销 / 排行 / 记录筛选排序 |
| `layout.py` | 座位布局：列分组、过道、讲台、行列与网格换算 |
| `lottery.py` | 抽奖：资格筛选、权重抽取、抽取人数、随机抽人 |
| `videos.py` | 抽奖视频仓库 + 「奖项 → 动画/视频」绑定表 |
| `devices.py` / `pairing.py` / `fingerprint.py` | 多端：已配对设备表、配对令牌、班级短指纹 |
| `oplog.py` | 操作流水账：多端各存副本时用它合并改动 |
| `paths.py` | 数据目录定位，允许把数据挪到任意盘符 |
| `theme_store.py` | 本机「主题相关」的那几组文件 |

### 3.2 `app/io/` —— 导入导出

| 模块 | 职责 |
| --- | --- |
| `csv_io.py` | CSV：学生名单与积分记录 |
| `xlsx_io.py` | XLSX：学生名单 + 积分记录两个 sheet |
| `docx_io.py` | DOCX：`班级积分报告` |
| `backup.py` | 整库备份 / 恢复：带 manifest 的 zip 归档 + 轮转 |
| `legacy.py` | 旧版（网页版）备份文件的兼容解析 |

### 3.3 `app/services/` —— 协调层

界面只喊「我要加分」，具体怎么记、怎么落盘、怎么闪、怎么撤，全在这一层。

| 模块 | 职责 |
| --- | --- |
| `timer_registry.py` | 延迟动作的统一登记表（全局唯一调度入口） |
| `voting.py` | 投票控制器：早读 / 午读「点座位给分」的唯一入口 |
| `pulse.py` | 座位高亮与加/减分动画的唯一调度者 |
| `autosave.py` | 自动保存：让保存不需要人操心，但失败一定看得见 |
| `assets.py` | 运行时生成界面用的小图标 |
| `qr.py` | 二维码：手机链接 → 点阵，交给界面去画 |
| `media_decode.py` | 内置 ffmpeg 解视频：探测、时长/帧率、逐帧解码、PCM 音频 |
| `media_probe.py` | 视频「能不能播」的预检与错误翻译 |
| `transcode.py` | 把内置播放器解不开的视频转成 H.264/AAC 的 mp4 |
| `sync_server.py` | 局域网同步服务端：HTTP REST（`/api/state` `/api/students` `/api/leaderboard`） |
| `sync_client.py` | 桌面端「各存副本、互相同步」的客户端 |
| `sync_v27.py` | v2.7 多端互联 HTTP 路由：配对 / 设备管理 / 导出 / 通知 / 附件 |
| `tunnel.py` | 公网隧道（cloudflared）：异网访问 |
| `domain_setup.py` | 一键部署固定域名：把固定隧道的几步串成流水线 |
| `diagnose.py` | 多端连接体检：把「为什么连不上」拆成几条能自己看的结论 |

### 3.4 `app/ui/` —— 界面层

绝大多数文件的目标写在 docstring 里：**1:1 复刻原版 HTML 的某个选择器**。

- `theme.py`：配色与尺寸，全部取自原版 `:root` 令牌与实测渲染值（唯一视觉常量来源）。
- `main_window.py`：主窗口，复刻三段式骨架（topbar / board / bottombar）。
- `modals.py`：通用模态壳（`.modal / .modal-head / .modal-body / .modal-foot`）。
- `seat_map.py` / `seat_map_cell.py`：主界面座位表与座位格（112×66、圆角 12、底色 `#fbfcfe`）。
- `stage.py` / `stage_seat_cell.py`：早读 / 午读全屏舞台与全屏座位格。
- `leaderboard.py` / `history_view.py` / `lottery_view.py` / `manual_dialog.py`：排行榜、记录、抽奖、加减分弹窗。
- `widgets/`：toast、通知浮层、流式布局、日历、学生选择器、启动动画、视频选择/播放。
- `dialogs/`：设置、布局、奖品、导入导出、设备管理、更新日志、关于、自定义图标等。

**媒体链路**值得单独说：Windows 上 DirectShow 常常没有 H.264 解码器，所以
`widgets/ffmpeg_player.py` 用自带 ffmpeg 解码、Qt 自绘；`video_player.py` 在其上做
「预览 / 试播 / 抽奖揭晓」三种播放形态，失败时必须说清原因，而不是黑屏。

### 3.5 `app/selftest/` —— 内置自检

给最终用户（老师）用的「体检」：`checks.py` 是每一条检查，`runner.py` 负责执行与汇总。
覆盖数据目录可写、状态往返、备份可读、记分撤销往返、布局数学、定时器作用域、
脉冲代际、磁盘空间、二维码、媒体后端、转码器、单实例等 12 项。

### 3.6 `app/main.py` —— 装配层

刻意只做三件事：加载数据、建主窗口、把退出清理收在一处。

- 不在这里改业务数据（交给 `core` / `services`）；
- 不在这里挂定时器（交给 `TimerRegistry`）；
- 单实例：本地套接字 `classpoints-single-instance-8f3a`，重复启动会激活已有窗口后退出。

### 3.7 `班级积分系统.py` —— 入口

- 把源码根目录塞进 `sys.path`，支持直接 `python 班级积分系统.py`；
- 打包成 `--noconsole` 后 `sys.stdout/stderr` 是 `None`，这里把它们接到
  `%APPDATA%\班级积分系统\app.log`，否则槽里一个未捕获异常就会让进程 abort、闪退且无日志；
- 未捕获异常统一落 `crash.log`，提示里带路径；
- `--selftest` 直接在命令行跑自检。

---

## 4. 两条典型数据流

### 4.1 一次「加分」

```
座位单击 (ui/stage.py · seat_map.py)
      → services/voting.py        记一笔投票 / 找学生
      → core/scoring.py           纯函数：改分、生成撤销条目
      → core/state.py             标脏 + 通知监听者
      → services/autosave.py      节流落盘
      → core/storage.py           原子写 + 备份轮转
      → services/pulse.py         座位高亮/动画（通过 TimerRegistry 延时回收）
```

撤消走同一条链的 `scoring.undo_entry(state, entry_id)`，撤销条目本身也是一条记录。

### 4.2 一次「早读舞台」

```
进入舞台 (ui/main_window.py)
      → ui/stage.py · open_stage(source)
      → services/voting.py · begin_session(state, source)
      → 整表重建（只在进入时发生一次，避免每帧重建）
      → 点击座位 → voting.tap(sid) → 同上「加分」链路
退出 (close_stage)
      → 先收定时器，再归零所有格子动画，最后发一行统计 toast
```

---

## 5. 安装器（`installer/`）

安装器是**独立的** PyInstaller 程序，刻意不 import `app` 包——它要能安装一个
还没被安装的程序。

| 模块 | 职责 |
| --- | --- |
| `main.py` | 入口：带参数走命令行（`--silent` / `--version`），不带参数弹向导 |
| `install_core.py` | 安装/卸载核心逻辑（纯 stdlib，不含 Qt，也不 import app） |
| `constants.py` | 共享常量（应用名、注册表项、安装目录约定等） |
| `bundle.py` | 资源定位：打包态读 PyInstaller 的 `_MEIPASS`，开发态读构建暂存目录 |
| `winsys.py` | Win32 交互：系统文件夹、磁盘空间、结束进程、打开目录（ctypes） |
| `win_shortcut.py` | 快捷方式：COM `IShellLinkW` / `IPersistFile` 建/删 `.lnk`，PowerShell 兜底 |
| `build_payload.py` | **构建期**：把 onedir 产物压成 `payload.zip` 并写 `manifest.json` |
| `uninstaller.py` | `Uninstall.exe` 入口：删程序文件 / 快捷方式 / HKCU 卸载登记，**保留班级数据** |
| `ui/pages.py` | 四页：欢迎 → 选择目录 → 安装进度 → 完成 |
| `ui/wizard.py` | QWizard 主窗口：流程、按钮文案、安装态约束 |
| `ui/worker.py` | 把安装逻辑放到后台线程，避免解压 100+ MB 时界面假死 |
| `ui/style.py` | 统一观感（QSS + 字体） |

### 5.1 为什么有 `build_payload`

PyInstaller 的 `--add-data` 把 125 MB 的 onedir 目录直接塞进 onefile，启动时要解包成
几千个小文件，冷启动很慢。所以构建期先压成单个 `payload.zip`，安装时用 Python
`zipfile` 解压（中文名按 UTF-8 处理）。`manifest.json` 记录版本、文件数、压缩前后
字节数、sha256 与构建时间，安装器据此校验完整性。

### 5.2 卸载的边界

删程序文件、删快捷方式、删 HKCU 卸载登记；**班级数据目录（`%APPDATA%\班级积分系统`）
一律保留**，并在卸载完成页告诉用户数据还在哪里。

---

## 6. 打包链（`packaging/`）

| 文件 | 作用 |
| --- | --- |
| `classpoints.spec` | 主程序，**onedir**（`app.exe` + `_internal/`） |
| `uninstaller.spec` | 卸载器，不打包 Qt（确认框走 `ctypes.windll.user32.MessageBoxW`），体积最小化 |
| `installer.spec` | 安装器，**onefile**（`setup.exe`），datas 带 `payload.zip` / `manifest.json` / `Uninstall.exe` / `app.ico` |
| `build.ps1` | 先打主程序 onedir，再打卸载器 |
| `build_installer.ps1` | 调 `installer/build_payload.py` 生成 payload，再打 `setup.exe` |

两个 `.ps1` **必须 UTF-8 带 BOM**（否则 PowerShell 5.1 会把中文读成乱码），
`_tools/ensure_bom.py` 负责自检与补齐。构建步骤与 manifest 字段见 `docs/BUILD.md`。

---

## 7. 怎么跑、怎么验

### 7.1 直接跑

```bash
python 班级积分系统.py            # 启动界面
python 班级积分系统.py --selftest # 跑内置自检
```

### 7.2 重建校验（本工程的「验收线」）

本工程的源码是从 `recover/` 里的 .pyc 逐函数复原的，因此有一套字节码级校验：

```bash
PY=/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14
$PY tools/verify.py --all              # 主程序 78 个模块
$PY tools/verify_installer.py --all    # 安装器 16 个模块
```

判定分两级：

1. **结构一致（必需）**：同一 `qualname` 的 code object 集合相同，指令序列
   （opname + 解析后的 argval）逐条相同，`co_names` / `co_varnames` / 参数个数一致
   —— 等价于「逻辑、常量、调用顺序、分支结构都一样」。
2. **字节一致（加分）**：`co_code` 完全相同。

当前状态：主程序 **78/78**（26 个字节一致、52 个结构一致）；安装器 **16/16**
（10 个字节一致、6 个结构一致）——两侧全部通过，安装器与主程序处在同一还原标准。

### 7.3 Windows 上重建安装包

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

产物：`dist\班级积分系统\`（onedir）、`dist\Uninstall.exe`、`dist\setup.exe`。

---

## 8. 阅读建议

按这个顺序读，最省力：

1. `app/version.py` → `app/core/models.py`（先知道数据长什么样）；
2. `app/core/state.py` → `app/core/storage.py`（数据怎么存、怎么通知）；
3. `app/services/voting.py` + `app/core/scoring.py`（最核心的一条业务链）；
4. `app/ui/main_window.py` → `app/ui/seat_map.py`（界面怎么搭起来）；
5. `app/services/timer_registry.py` + `app/services/pulse.py`（延迟动作与动画）；
6. 需要改视觉时只看 `app/ui/theme.py`；需要改导出时看 `app/io/`；
7. 安装器从 `installer/install_core.py` 读起，再看 `ui/pages.py`。

---

*本文件由复原工程生成，与 `docs/BUILD.md`（打包细节）、
`tools/HOWTO-REBUILD.md`（复原方法）、`tools/HOWTO-INSTALLER.md`（安装器复原要点）配套阅读。*
