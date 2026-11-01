# installer 包还原作业（setup.exe 内层安装器）

## 背景

`setup.exe` 不是 Inno/NSIS，而是一个 **PyInstaller onefile 的 Python 自制安装器**：
- 顶层入口 `recover/setup.exe_extracted/main.pyc` 与 `installer/main.pyc` **字节逐字相同**
  （spec 直接拿 `installer/main.py` 当 entry，所以校验器里 `main` 与 `installer.main`
  映射到同一个源码文件，两个都必须各自通过）。
- 被安装的应用 `班级积分系统.exe` 是 PyInstaller **onedir**：`recover/payload/_internal/`
  就是它的运行时目录（python314.dll、PyQt5、segno、docx、lxml、cloudflared.exe…）。

## 素材与验收

| 素材 | 路径 |
|---|---|
| 原始 pyc | `recover/setup.exe_extracted/PYZ.pyz_extracted/installer/**.pyc`、`recover/setup.exe_extracted/main.pyc` |
| 全量反汇编（已生成，勿重造） | `/tmp/inst.<模块名带点>.txt`，如 `/tmp/inst.installer.winsys.txt` |
| 重建目标 | `classpoints-py/installer/**.py`（`ui/` 是子包） |
| 验收命令 | `/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14 tools/verify_installer.py <模块名> [-v]` |

判定口径与 app 侧 `tools/verify.py` 完全相同（verify_installer.py 直接复用其函数）。


## install_core 实证新坑（ui-main-builder，转全队）

1. 模块级带注解赋值（`STEPS: tuple[Step, ...] = (...)`）自动生成 `__conditional_annotations__`
   cell + BUILD_SET 头，源码什么都不用额外写（HOWTO 第 8 条实测）。
2. `if callback is None: return lambda **kwargs: None` —— flags=0x1b/varnames=('kwargs',)
   只有 `**kwargs` 形参编得出来（`*a`→0x17、两者→0x1f、裸 lambda→0x13）。
3. `x if x is not None else [s.key for s in STEPS].index(...) + 1` 这类长三元+内嵌推导式
   **整行不可折**（折行打散 STORE_FAST_LOAD_FAST 融合对）。
4. dataclass 类体：带默认值+注解字段是「STORE_NAME 先、__annotations__ 后」；
   纯注解字段只有 STORE_SUBSCR。交错顺序照 dis 行号写。
5. `if (target / APP_ICON).is_file() is False: pass`、`if check.warning: pass` 这类
   **空 pass 分支是原文**，删掉就少一对跳转。
6. `@dataclass(frozen=True)` 的指令序：先 LOAD_NAME dataclass/PUSH_NULL/LOAD_CONST True/
   CALL_KW，再 LOAD_BUILD_CLASS——装饰器紧贴类定义。
7. dataclass 类体 co_consts 顺序（内容相同顺序不同）属 verify 忽略的排版项，可接受。

## uninstaller 实证新坑（ui-dialogs-builder，字节一致 37/37）

1. dis 里 `LOAD_FAST 融合对 (a,b)` + 单个 `LOAD_ATTR resolve` ⇒ 原码 `a == b.resolve()`
   （左侧不带方法调用）。
2. `_write_log([A if c else f'B:{x}'])` 三元在 **list 字面量内部**（BUILD_LIST 1 只出现一次），
   不是两个列表分支。
3. `argv if argv is not None else sys.argv[1:]` 与 `sys.argv[1:] if argv is None else argv`
   的 POP_JUMP_IF_NONE / NOT_NONE 方向不同，按 dis 的 jump 方向抄。


## worker 实证新坑（core-fixer，转全队）

1. `try/except InstallError/except Exception/else` 四段结构：`succeeded.emit` 放
   **else 子句**时，dis 里 try 头会有 NOP@L(try行)、各 except 分支尾部是隐式 return；
   else 分支的存在与否直接改变指令流形态。
2. 类体信号定义分块（多个 `pyqtSignal(...)` 类属性）各占独立 L 行号，别合并写。
3. `__init__(options: install_core.InstallOptions, parent=None)`：`(None,)` kwdefaults +
   `__class__` cell（`COPY_FREE_VARS`/`LOAD_SUPER_ATTR`）⇒ 源码里有 `super().__init__(parent)`。

## 分工与状态

| 模块 | code objects | 反汇编大小 | 负责 | 状态 |
|---|---|---|---|---|
| installer.__init__ | 1 | 2K | lead | ✅ 字节一致 |
| installer.__main__ | 1 | 2K | lead | ✅ 字节一致 |
| installer.constants | 1 | 4K | lead | ✅ 字节一致 |
| installer.ui.__init__ | 1 | 0.4K | lead | ✅ 字节一致 |
| installer.ui.style | 1 | 11K | lead | ✅ 字节一致 |
| installer.ui.worker | 6 | 12K | core-fixer | ✅ 结构一致 |
| installer.bundle | 15 | 19K | svc2 | 待做 |
| installer.build_payload | 13 | 57K | svc2 | 待做 |
| installer.main | 16 | 46K（含 main 双查） | svc2 | 待做 |
| installer.win_shortcut | 29 | 67K | io2 | 待做 |
| installer.winsys | 34 | 68K | io2 | 待做 |
| installer.ui.wizard | 14 | 39K | io2 | 待做 |
| installer.uninstaller | 37 | 102K | ui-dialogs-builder | ✅ 字节一致 |
| installer.install_core | 62 | 188K | ui-main-builder | ✅ 结构一致 |
| installer.ui.pages | 53 | 150K | core-fixer | ✅ 结构一致 |

⚠️ 只写自己表内那一行对应的文件；`tools/`、`recover/` 不许动。
⚠️ `main` 模块与 `installer.main` 是同一份源码被校验两次，owner（svc2）对两者都负责。

## 方法论（app 侧 78/78 全靠这些，原文见 tools/HOWTO-REBUILD.md）

1. 反汇编是唯一权威：先读 `/tmp/inst.*.txt` 的 `varnames/names/consts` 再写源码，别猜。
2. `LOAD_FAST_BORROW_LOAD_FAST_BORROW ('a','b')` 融合只发生在**同一物理行** → 实参换行分组照 dis 的 L 标记写。
3. `BUILD_STRING n` = f-string；`BINARY_OP 0/5/11/26` = `+ * - []`。
4. `except X: return` vs `pass` vs `break` 改变 co_names 登记顺序；`except (A, B)` 是 tuple 常量。
5. 控制流猜不出来就 dump `dis._parse_exception_table(code)`，把 start/end/target 换算成
   保留指令序号（过滤 CACHE/EXTENDED_ARG 后的下标），可直接读出 try/except/finally 嵌套。
6. `if cond:` 与函数体之间插入空行/注释行会产生一条额外 `NOP`；`if x: … else: pass`
   会产生 `JUMP_FORWARD` 跳过 `NOP`。
7. 注解必须写在源码里（`from __future__ import annotations` 下注解按原文、不加引号）；
   带默认值的参数必须带注解，否则 `__annotate__` map 少项。
8. 模块级带注解赋值（`X: list[T] = [...]`）会生成 `__conditional_annotations__` cellvar
   与模块头 `MAKE_CELL/BUILD_SET/SETUP_ANNOTATIONS`——不要手写模块头凑指令。
9. 单函数快速迭代用 `/tmp/tn/probe2.py`（app 侧工具；installer 模块同理可用：
   `$PY /tmp/tn/probe2.py` 需要 ROOT 下能按模块名找到 pyc —— installer 的 pyc 在
   `recover/setup.exe_extracted/PYZ.pyz_extracted/`，app 侧路径找不到时用
   `tools/verify_installer.py <mod> -v` 即可，它输出 unified diff 同款差异行）。
10. docstring 必须逐字（它是常量）。中文引号「」别改成 ""。
