# 重建作业指引（字节码级还原 .pyc → .py）

目标：让 `classpoints-py/` 下每个重建出的 `.py` 与原始 `.pyc` **指令级一致**。
不是"差不多能用"，而是编译后的字节码与原程序逐条相同——这是本工作包唯一
可靠的正确性判据，也是验收标准。

## 目录与素材

工作根目录：`/root/Documents/deepseek-harness/default-workspace/classpoints-work`

| 素材 | 路径 | 用途 |
|---|---|---|
| 原始字节码 | `recover/app.exe_extracted/PYZ.pyz_extracted/<模块路径>.pyc` | 校验基准（勿直接改） |
| 骨架清单 | `recover/export/skeleton/<模块名>.txt` | defs 列表(行号/qualname/参数) + 全部字符串常量原文 |
| 反汇编 | `recover/export/dis/<模块名>.dis.txt` | 逐条指令 + 常量索引，**这是权威依据** |
| 重建产物 | `classpoints-py/<模块路径>.py` | 你写的文件 |

## 校验命令（必须用它验收）

```bash
cd /root/Documents/deepseek-harness/default-workspace/classpoints-work
/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14 tools/verify.py <模块名...>
/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14 tools/verify.py <模块名> -v   # 差异逐条打印
```

`-v` 输出的 unified diff：`---` 行是原始指令，`+++` 行是你当前源码编译出的指令。
判定 = 指令序列（opname+解析后的操作数）一致、co_names/varnames/参数个数/flags 一致。
跳转目标已按"第几条指令"归一、类体 `__firstlineno__` 行号已剔除，**不用**为行号操心。

## 作业方法（按此顺序做，别跳步）

1. 读 skeleton：拿到 defs 列表（含原始行号顺序）与全部字符串常量原文。
   模块 docstring、每个函数的 docstring 都必须从 skeleton/dis 里**原样**取——
   它们是常量，差一个标点就判失败。dis 里 `LOAD_CONST 0 ('...')` 是权威文本。
2. 读 dis 的 `<module>` 段：确定 import 列表（`IMPORT_NAME`/`IMPORT_FROM`/
   `STORE_NAME` 给出别名，必须逐字一致，如 `from app import version as version_mod`）、
   模块级常量的名字与值、每个 def 在源码里的先后顺序。
3. 每个函数体：对照 dis 里该 code object 的逐条指令写代码。指令流里的
   行号标记（如 `24` 单独一列）帮你分块。常量索引 `LOAD_CONST 3 ('utf-8')`
   的字符串内容直接来自同一行的括号。
4. 写完一个函数就整体验一次（verify 按模块跑），用 `-v` 的 diff 定位。
   不要憋整写完再验——大模块（settings_dialog/main_window）分段落逐段验证。

## 已验证的坑（都踩过，直接对照检查）

1. **默认参数**：定义处若有多余的 `LOAD_CONST (None,)` 或元组常量 + `MAKE_FUNCTION`
   前有默认值常量，说明原函数**没有/有**默认值。反例：`_file(root)` 原本没有
   `= None` 默认值，凭空加了就挂。看 dis 里 `MAKE_FUNCTION` 前的 LOAD_CONST。
2. **仅关键字参数**：`co_argcount: 1 vs 7 | co_kwonlyargcount: 6 vs 0` 说明原函数
   是 `def f(a, *, b=..., ...)`。dis 里 MAKE_FUNCTION 前 `SET_FUNCTION_ATTRIBUTE 2 (kwdefaults)`。
3. **条件反转**：`POP_JUMP_IF_NOT_NONE` vs `POP_JUMP_IF_NONE` = 写成了
   `if x is None:` 分支互换，或 `if x is not None:` 写成带 else 的反向。
4. **in / not in**：`CONTAINS_OP 0 vs 1`。同理 `is not`、`!=` 这类比较符取反。
5. **注解字符串**：`from __future__ import annotations` 下注解是字符串常量，
   `list[Student]` vs `list[Student] | None` 差一个 `| None` 就失败。照抄。
6. **循环结构**：`FOR_ITER` 跳转目标不同 → 原码是 `if cond: continue` 而
   你写成 `if not cond: ...`（或反之），或 for-else 放错位置。
7. **累加 vs join**：`LOAD_FAST_BORROW 'acc' + BINARY_OP 0` = 原文用 `acc += ...`
   而不是 `''.join(...)`。字符串拼接方式改变指令流。
8. **f-string**：`FORMAT_SIMPLE` / `BUILD_STRING` / `CONVERT` (!r/!s)，与
   `.format()`、`%` 不是一回事，照 dis 写。
9. **`__annotate__` 的 co_consts[1]**：`'return'` vs 参数名 —— 注解函数返回类型
   与参数注解顺序照 dis 来；忘写返回注解就变 `('return', ...)` 缺项。
10. **装饰器**：`@classmethod/@property/@staticmethod/@dataclass(...)`——
    dis 里 MAKE_FUNCTION 之后紧跟对装饰器的 `CALL`；`@dataclass` 带参数时看常量。
    dataclass 字段顺序、默认值类型（`field(default_factory=list)`）逐字对照。
11. **三元表达式 vs if 语句**：条件表达式（`x = a if c else b`）生成
    `POP_JUMP_IF_FALSE` + 单线赋值；写成 if/else 块指令流不同。dis 说了算。
12. **walrus (`:=`)**：指令流里赋值在表达式内部出现 `copy`(COPY 1 / STORE_FAST 顺序)
    与外层不同，dis 里表现为先 STORE_FAST 后再参与比较。
13. **异常结构**：`SETUP_FINALLY`/`PUSH_EXC_INFO`/`CHECK_EG_MATCH`/
    `SWAP`/`COPY 4` 对应 try/except/else/finally、except*、with。
    `except (A, B)` 用 tuple 常量；`except A as e` 有 STORE_FAST。
14. **推导式/生成器**：独立 code object，qualname 形如 `f.<locals>.<genexpr>`；
    `yield` 函数 flags 带 GENERATOR（co_flags 不同即挂）。lambda 同理是 code object。
15. **None 的隐式 return**：函数尾 `LOAD_CONST None; RETURN_VALUE` 是默认，
    显式 `return`（无值）与之等价，不必纠结；但**提前** `return None` 与
    `return` 都编译成 `LOAD_CONST None; RETURN_VALUE`，位置必须对。
16. **`with A() as x` vs `with A():`**：有无 STORE_FAST 的区别。
17. **常量折叠**：`1+2` 在源码里可能折叠成 3；dis 显示什么常量就写什么。
18. **不要引入原码没有的中间变量**（co_varnames 会多）；局部变量名也要逐字一致。
19. **行内注释/空行/换行排版随意**（不进字节码），但**文档字符串是常量**，必须原文。
20. 嵌套 def 的 qualname 是 `外层.<locals>.内层`；verify 按 qualname 配对，
    原码把函数定义在函数里，你在模块级写就配不上对（缺少/多出 code objects）。
21. **LOAD_FAST 融合按物理行成对**（3.14）：`LOAD_FAST_LOAD_FAST` /
    `LOAD_FAST_BORROW_LOAD_FAST_BORROW` 只合并同一源码行的相邻局部变量读取；
    连续 3 个实参编成「一对+单个」。调用实参的换行分组要与原始一致，
    skeleton/dis 的行号标记就是排版线索。
22. `a if a else b`（显式三元）= TO_BOOL+POP_JUMP_IF_TRUE 且操作数重读两次；
    `a or b` = COPY 1 / POP_JUMP_IF_TRUE / POP_TOP。别互相代写。
23. `x = A if cond else B` 与 `x = B if not_cond else A` 的分支排布不同，
    以 dis 的 POP_JUMP 方向为准。
24. `if x and not f(y)` ≠ `if x and f(y) is None`（POP_JUMP_IF_NOT_NONE vs
    TO_BOOL+POP_JUMP_IF_TRUE）；同理 `and`/`or` 短路位置。
25. dis 里没有 `MAKE_CELL` 但该参数是自由变量 → 局部变量名顺序不同，
    对照 co_varnames 差异定位。
26. lambda 的默认参数名也要逐字一致（原码有 `_=False` 这类写法）。
27. f-string 字面 `{`/`}` 写 `{{`/`}}`；常量文本逐字来自 dis 括号内。
28. **非 ASCII 单字常量核对 codepoint**：同形异码（U+F900 兼容表意文字 vs
    U+8C48 等）verify 报错肉眼不可分；生僻字符串先 `hex(ord(c))` 再决定
    字面或 `\uXXXX` 转义。
29. **带默认值的参数必须带注解**：漏注解会让 `__annotate__` 的 map 少一项
    （报 `'x' vs 'return'`）；dis 中 `LOAD_CONST('x')`+`LOAD_CONST('类型')`
    相邻对是注解证据。无注解参数整个从 map 消失。`'return'` 恒在 map 末尾。
30. try/finally 里要从内层 try **`return`**（不是 break）才会生成 finally
    前置副本；cleanup 的 except 体若是 `pass` 不要写成 `return`。
31. 连乘 `a*b*c`：原码 LOAD_FAST 成对融合时，源码的括号/同行写法要一致
    （`a*(b*c)` 与 `(a*b)*c` 指令不同）。
32. `if not (a or b or c): X; continue` 这类控制流与 if/else 互换版本 jump
    方向不同，逐条对 dis 的 POP_JUMP 方向与 NOT 指令。

## 通过标准

- `tools/verify.py <你的模块> `全部 ✓（结构一致即可验收；字节一致更好）。
- 不许改 `tools/verify.py`、`recover/` 下的任何文件来"通过"。
- 只写自己 scope 内的 `.py` 文件；不要动别人的文件。
- 已通过的模块（tools/baseline.txt 里 ✓ 的）不许碰。

## 参考风格

已通过校验的成品可当范本：`classpoints-py/app/core/oplog.py`、
`app/core/devices.py`、`app/services/qr.py`、`entry_main`（=classpoints-py/班级积分系统.py）。
它们的共同点：中文注释齐全、docstring 与常量原文一致、分层导入
（core 不 import ui；services 只 import core）。
33. **superinstruction 融合只在两条指令位于同一源码行时发生**（CPython 3.14
    `flowgraph.c` 的 `make_super_instruction()`：`line1 != line2` 直接返回）。
    所以 `STORE_FAST_LOAD_FAST ('info','info')` 出现 ⇒ 源码里 `for info in ...`
    与元素表达式在**同一行**；原版是 `STORE_FAST 'info'` + 独立
    `LOAD_FAST_BORROW 'info'` ⇒ 那是**跨行**写的推导式（如 `[...\n for info in ...]`）。
    同理函数调用实参若逐个换行，就不会融合成
    `LOAD_FAST_BORROW_LOAD_FAST_BORROW ('a','b')`；单行写才会融合。
34. **执行顺序**：`optimize_cfg` → `insert_superinstructions` → … →
    `optimize_load_fast`。融合先于 BORROW 转换，所以原版出现
    `LOAD_FAST_BORROW` 说明当时它是 `LOAD_FAST` 且未融合（跨行或 oparg≥16）。
35. `try: ... finally: pass` 生成 `PUSH_EXC_INFO / RERAISE 0 / COPY 3 /
    POP_EXCEPT / RERAISE 1`（无 POP_TOP、无 RAISE_VARARGS）；
    裸 `except: raise` 生成 `PUSH_EXC_INFO / POP_TOP / RAISE_VARARGS 0 /
    COPY 3 / POP_EXCEPT / RERAISE 1`；`except Exception: raise` 还会多出
    co_names 'Exception'。三者要按 dis 精确区分。
36. 三元表达式 `X if c else Y` 的两分支读取模式不同（X 分支 `LOAD_FAST`，
    Y 分支 `LOAD_FAST_BORROW`）；写成 `if c: return X` + `return Y` 两段式
    则两条都是 BORROW。同一条表达式里同一变量读两次 → 第一次 BORROW、
    第二次 FAST（"borrow then owned"）。
37. `try:` 块内被 `STORE_FAST` 的变量，在块外引用时是 `LOAD_FAST`；
    try/except **带 `continue`** 也一样。
38. 模块里有**模块级带注解变量**（`X: list[int] = []`）且文件头有
    `from __future__ import annotations` 时，模块 co_cellvars =
    `('__conditional_annotations__',)`，字节码以
    `MAKE_CELL / BUILD_SET 0 / STORE_NAME '__conditional_annotations__' /
    SETUP_ANNOTATIONS` 开头（在 docstring 之前）。
39. 带默认值的参数必须带注解，否则 `__annotate__` map 少一项；注解是
    字符串常量（`from __future__ import annotations` 下全部字符串化）。
40. **验证工具**：`tools/verify.py <mod> [-v]`、`tools/diffmod.py <mod> [名字过滤]`
    （逐 code object 的 unified diff，最有效）、`tools/cmpfn.py <mod> <qualname>
    <candidate.py>`（把候选函数源码单独编译并与原版逐条比对 + 异常表）。
    Python 解释器固定用 `/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14`。
