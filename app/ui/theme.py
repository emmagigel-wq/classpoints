"""配色与尺寸：全部取自原版 HTML 的 :root 令牌与实测渲染值。

规格来源（不许凭感觉改这里）：
  * ui_ref/css-rules.txt 第 1 行 —— :root 自定义属性原样；
  * ui_ref/nodes-default.txt / nodes-stage-zaodu.txt —— headless Edge
    在 1440x900 下的实测 computed style（px 值）。
界面代码不许写死色值，一律从这里取。
"""

from __future__ import annotations

# —— 基础色板 ——
BG = '#eef2f8'
CARD = '#ffffff'
INK = '#101828'
INK2 = '#475467'
INK3 = '#98a2b3'
LINE = '#e4e7ec'
BRAND = '#4c6ef5'
BRAND_DARK = '#3b5bdb'
OK = '#12b76a'
OK_BG = '#e7f8f0'
BAD = '#f04438'
BAD_BG = '#feeceb'
WARN = '#f79009'
WARN_BG = '#fff6e6'
RADIUS = 14

# —— 座位 ——
SEAT_BG = '#fbfcfe'
SEAT_HOVER_BG = '#ffffff'
SEAT_HOVER_BORDER = '#c7d2fe'
SEAT_EMPTY_BORDER = '#eef0f4'
SEAT_EDIT_BORDER = '#b9c0ff'
SEL_RING = 'rgba(76, 110, 245, 0.18)'
BIG_BG = '#eef2ff'
BIG_BORDER = '#dbe3ff'
BIG_INK = '#3538cd'
BTN_GHOST_BG = '#f9fafb'
OK_BTN_BG = '#e7f8f0'
OK_BTN_BORDER = '#bdf0d6'
OK_BTN_INK = '#027a48'
BAD_BTN_BG = '#feeceb'
BAD_BTN_BORDER = '#fbd3d0'
BAD_BTN_INK = '#b42318'
PILL_GRAY_BG = '#f2f4f7'
SEL_ACTIVE_BG = '#eaf0ff'
PODIUM_TOP = '#fff9e8'
PODIUM_BOTTOM = '#fdefc8'
PODIUM_BORDER = '#eab308'
PODIUM_BORDER_STAGE = '#f5d68a'
PODIUM_INK = '#b54708'
GOT_PLUS_BG = '#c9f5df'
GOT_MINUS_BG = '#fdd7d4'
GOT_GOLD_BG = '#fde9b8'
GOT_GOLD_LINE = '#e6a700'
RANK_BAR_A = '#a5b4fc'
RANK_BAR_B = '#4c6ef5'
LOGO_A = '#4c6ef5'
LOGO_B = '#7c3aed'
TOAST_BG = 'rgba(16, 24, 40, 0.92)'
TOAST_OK = '#027a48'
TOAST_BAD = '#b42318'
TOAST_WARN = '#b54708'
STAGE_BG = '#f6f8fc'

RANK_TOP = {
    1: ('#f7b733', '#fc4a1a'),
    2: ('#c3cfe2', '#8fa3bf'),
    3: ('#e8a87c', '#c38d63'),
}

FONT_FAMILY = '"Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Segoe UI", sans-serif'
FONT_FAMILY_QT = 'Microsoft YaHei'
FONT_BASE = 15

TOPBAR_H = 68
BOTTOMBAR_H = 83
BOARD_PAD_X = 20
BOARD_PAD_Y = 16
BOARD_GAP = 16
RANK_W = 320
CARD_HEAD_SEAT_H = 51
CARD_HEAD_RANK_H = 45
SEATMAP_PAD = 16
SEAT_GAP = 10
AISLE_W = 22
AISLE_W_STAGE_EM = 0.6
SEAT_MIN_H = 66
SEAT_MIN_W = 64
RANK_ITEM_H = 40
RANK_RK = 24
STAGE_PAD_TOP = 14
STAGE_PAD_LEFT = 18
STAGE_HEAD_H = 60

TEXT = INK
TEXT_WEAK = INK2
BORDER = LINE
PRIMARY = BRAND
PRIMARY_DARK = BRAND_DARK
SUCCESS = OK
DANGER = BAD
WARNING = WARN
GOLD = '#f7b733'
POINT_TIERS = []


def tier_of(points: int) -> tuple[str, str]:
    """原版座位底色与分数无关（分数只改文字颜色）。保留函数签名供旧调用。"""
    return (SEAT_BG, LINE)


def qgrad(x1: float, y1: float, x2: float, y2: float, a: str, b: str) -> str:
    """QLinearGradient 字符串。135deg 对角 ≈ (0,0)→(1,1)。"""
    return f'qlineargradient(x1:{x1}, y1:{y1}, x2:{x2}, y2:{y2}, stop:0 {a}, stop:1 {b})'


GRAD_LOGO = qgrad(0, 0, 1, 1, LOGO_A, LOGO_B)
GRAD_PODIUM = qgrad(0, 0, 0, 1, PODIUM_TOP, PODIUM_BOTTOM)
GRAD_RANK_BAR = qgrad(0, 0, 1, 0, RANK_BAR_A, RANK_BAR_B)


def rank_top_grad(n: int) -> str:
    a, b = RANK_TOP[n]
    return qgrad(0, 0, 1, 1, a, b)


APP_QSS = f'''
QWidget {{
    background: {BG};
    color: {INK};
    font-family: {FONT_FAMILY};
    font-size: {FONT_BASE}px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* —— 通用按钮（.btn：9px16 / r10 / 14px w600）—— */
QPushButton {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 9px 16px;
    font-size: 14px;
    font-weight: 700;
    color: {INK};
}}
QPushButton:hover {{ background: {BTN_GHOST_BG}; }}
QPushButton:pressed {{ background: {PILL_GRAY_BG}; }}
QPushButton:disabled {{ background: {BTN_GHOST_BG}; color: {INK3}; border-color: #d0d5dd; }}
QPushButton#ghost {{ background: transparent; border-color: {LINE}; }}
QPushButton#ghost:hover {{ background: {PILL_GRAY_BG}; }}
QPushButton#ghost[active="true"] {{
    background: {SEL_ACTIVE_BG}; border-color: {SEAT_HOVER_BORDER}; color: {BRAND_DARK};
}}
QPushButton#primary {{ background: {BRAND}; border-color: {BRAND}; color: white; }}
QPushButton#primary:hover {{ background: {BRAND_DARK}; border-color: {BRAND_DARK}; }}
QPushButton#danger {{ background: {BAD}; border-color: {BAD}; color: white; }}
QPushButton#tiny {{ padding: 4px 9px; font-size: 12px; border-radius: 7px; }}
QPushButton#mini {{ padding: 6px 12px; font-size: 13px; border-radius: 9px; }}

/* —— 输入控件 —— */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 8px 10px;
    font-size: 14px;
    color: {INK};
    selection-background-color: {BRAND};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {BRAND}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}

/* —— 卡片 —— */
QFrame#card {{
    background: {CARD};
    border: 1px solid {LINE};
    border-radius: {RADIUS}px;
}}
QLabel#cardTitle {{ font-size: 15px; font-weight: 700; color: {INK}; background: transparent; }}
QLabel#hint {{ font-size: 12px; color: {INK3}; background: transparent; }}

/* —— 文字语义 —— */
QLabel {{ background: transparent; }}
QLabel#ink2 {{ color: {INK2}; }}
QLabel#ink3 {{ color: {INK3}; }}
QLabel#sub {{ font-size: 12px; color: {INK2}; }}
QLabel#h1 {{ font-size: 19px; font-weight: 700; color: {INK}; }}
QLabel#pos {{ color: {OK}; }}
QLabel#neg {{ color: {BAD}; }}

QStatusBar {{ background: {CARD}; color: {INK2}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #cbd5e1; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #94a3b8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #cbd5e1; border-radius: 5px; min-width: 28px; }}
QScrollArea {{ background: transparent; border: none; }}
'''


def bottom_button(variant: str) -> str:
    """底部大按钮：早读/午读(big) / 加分(ok) / 扣分(bad) / 其余默认。"""
    if variant == 'big':
        bg, bd, fg = BIG_BG, BIG_BORDER, BIG_INK
        fs = 17
    elif variant == 'ok':
        bg, bd, fg = OK_BTN_BG, OK_BTN_BORDER, OK_BTN_INK
        fs = 16
    elif variant == 'bad':
        bg, bd, fg = BAD_BTN_BG, BAD_BTN_BORDER, BAD_BTN_INK
        fs = 16
    else:
        bg, bd, fg = BTN_GHOST_BG, LINE, INK
        fs = 16
    return (
        f'QPushButton {{ background: {bg}; border: 1px solid {bd};'
        f' border-radius: 12px; font-size: {fs}px; font-weight: 700; color: {fg}; }}'
        f'QPushButton:hover {{ background: {CARD}; border-color: #c7d2fe; }}'
        f'QPushButton:pressed {{ background: {PILL_GRAY_BG}; }}'
    )


def seat_qss(
    state: str,
    *,
    points_sign: int = 0,
    font_px: int = 15,
) -> str:
    """主界面座位格样式。state: normal/hover/selected/empty/editing。

**只接受枚举状态**——输入决定输出，回调顺序不可能造成两种外观（缺陷 4）。

字号必须取整：Qt 的样式表解析器只认整数 px，`font-size: 14.5px` 会让整条
声明被静默丢弃（不是报错，是当没写），于是该控件退回默认字体。
"""
    font_px = int(round(font_px))
    if state == 'empty':
        frame = f'QFrame#seat {{ background: transparent; border: 1px dashed {INK3}; border-radius: 12px; }}'
        name_c, add_c = INK3, '#cdd5e0'
    elif state == 'editing':
        frame = f'QFrame#seat {{ background: {SEAT_BG}; border: 1px dashed {SEAT_EDIT_BORDER}; border-radius: 12px; }}'
        name_c, add_c = INK, '#cdd5e0'
    elif state == 'selected':
        frame = f'QFrame#seat {{ background: {CARD}; border: 3px solid {BRAND}; border-radius: 12px; }}'
        name_c, add_c = INK, '#cdd5e0'
    elif state == 'hover':
        frame = f'QFrame#seat {{ background: {SEAT_HOVER_BG}; border: 1px solid {SEAT_HOVER_BORDER}; border-radius: 12px; }}'
        name_c, add_c = INK, '#cdd5e0'
    else:
        frame = f'QFrame#seat {{ background: {SEAT_BG}; border: 1px solid {LINE}; border-radius: 12px; }}'
        name_c, add_c = INK, '#cdd5e0'
    pt_c = (
        OK
        if points_sign > 0
        else (BAD if points_sign < 0 else INK2)
    )
    return (
        frame
        + f' QLabel#seatName {{ background: transparent; color: {name_c}; font-size: {font_px}px; font-weight: 700; }}'
        + f' QLabel#seatPoints {{ background: transparent; color: {pt_c}; font-size: {font_px}px; font-weight: 700; }}'
        + f' QLabel#seatAdd {{ background: transparent; color: {add_c}; font-size: 22px; font-weight: 300; }}'
    )


def stage_seat_qss(
    state: str,
    *,
    font_px: float = 40.0,
    name_px: float | None = None,
) -> str:
    """全屏座位格。state: normal/hover/got_plus/got_minus/empty。"""
    font_px = float(font_px)
    if state == 'empty':
        frame = f'QFrame#stageSeat {{ background: transparent; border: 2px dashed {SEAT_EMPTY_BORDER}; border-radius: 14px; }}'
        ink = INK3
    elif state == 'got_plus':
        frame = f'QFrame#stageSeat {{ background: {GOT_PLUS_BG}; border: 2px solid {OK}; border-radius: 14px; }}'
        ink = INK
    elif state == 'got_minus':
        frame = f'QFrame#stageSeat {{ background: {GOT_MINUS_BG}; border: 2px solid {BAD}; border-radius: 14px; }}'
        ink = INK
    elif state == 'hover':
        frame = f'QFrame#stageSeat {{ background: {CARD}; border: 2px solid {SEAT_HOVER_BORDER}; border-radius: 14px; }}'
        ink = INK
    else:
        frame = f'QFrame#stageSeat {{ background: {CARD}; border: 2px solid {LINE}; border-radius: 14px; }}'
        ink = INK
    nm = name_px if name_px is not None else font_px
    return (
        frame
        + f' QLabel#ssName {{ background: transparent; color: {ink}; font-size: {int(round(nm))}px; font-weight: 700; }}'
        + f' QLabel#ssPoints {{ background: transparent; color: {ink2_or(ink)}; font-size: {int(round(font_px * 0.32))}px; font-weight: 700; }}'
    )


def ink2_or(ink: str) -> str:
    return INK2 if ink == INK else ink


def calendar_qss() -> str:
    """日期/时间弹窗日历统一刷成白底。

用户反馈：「加分和扣分功能修改时间的日历表颜色」不对。QDateTimeEdit 的
QCalendarWidget 弹出后用的是原生配色，在深色任务栏/高对比主题下会变成
灰黑底、黑字，和整个应用的白底格格不入。这里显式钉成白底 + 深色字，
选中格用品牌蓝，周末/非当月日期用弱化的灰。
"""
    return (
        f'QCalendarWidget QWidget {{ background: {CARD}; color: {INK}; }}'
        f'QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {CARD}; border-bottom: 1px solid {LINE}; }}'
        f'QCalendarWidget QToolButton {{ background: transparent; color: {INK}; border: none; border-radius: 6px; padding: 2px 6px; }}'
        f'QCalendarWidget QToolButton:hover {{ background: {PILL_GRAY_BG}; }}'
        f'QCalendarWidget QSpinBox, QCalendarWidget QSpinBox:focus {{ background: {CARD}; color: {INK}; border: none; }}'
        f'QCalendarWidget QAbstractItemView {{ background: {CARD}; color: {INK}; outline: none; selection-background-color: {PRIMARY}; selection-color: #ffffff; }}'
        f'QCalendarWidget QAbstractItemView:disabled {{ color: {INK3}; }}'
        f'QCalendarWidget QMenu {{ background: {CARD}; color: {INK}; }}'
    )


def arrow_qss(icon_path: str) -> str:
    """下拉/日历箭头统一换成本项目自绘的 V 形图标。

为什么必须显式给：QSS 一旦接管了 QComboBox/QDateTimeEdit 的边框与内边距，
Qt 仍会用**原生样式**去画 drop-down 里的箭头子控件，于是出现
「箭头压在圆角边上、颜色跟文字不搭、高 DPI 下发糊」——用户看到的就是
「选择时间的图片错误」。这里把 down-arrow 的 image 钉成自带 png，
drop-down 区域只留宽度、去掉边框，箭头就稳定落在右侧内边距中间。
"""
    p = str(icon_path).replace('\\', '/')
    return (
        f'QComboBox::drop-down, QDateTimeEdit::drop-down, QAbstractSpinBox::drop-down {{ border: none; background: transparent; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}'
        f'QComboBox::down-arrow, QDateTimeEdit::down-arrow, QAbstractSpinBox::down-arrow {{ image: url(\'{p}\'); width: 10px; height: 10px; }}'
        f'QComboBox::up-arrow, QDateTimeEdit::up-arrow, QAbstractSpinBox::up-arrow {{ image: none; width: 0px; height: 0px; }}'
    )


def pill_qss(kind: str) -> str:
    """stage 头部统计药丸：plus/minus/undo/legend。"""
    table = {
        'plus': (OK, OK_BG, '#bdf0d6', 700),
        'minus': (BAD, BAD_BG, BAD_BTN_BORDER, 700),
        'undo': ('#b54708', '#fff6e6', '#fde3b3', 700),
        'legend': (INK2, CARD, LINE, 400),
        'legend_undo': ('#b54708', '#fff8ec', '#fde3b3', 400),
    }
    fg, bg, bd, wt = table.get(kind, table['legend'])
    if kind in ('legend', 'legend_undo'):
        bd = SEAT_EMPTY_BORDER
    return (
        f'QLabel {{ background: {bg}; color: {fg}; border: 1px solid {bd};'
        f' border-radius: 999px; padding: 6px 12px; font-size: 13px; font-weight: {wt}; }}'
    )


def podium_qss(
    stage: bool = False,
    font_px: float | None = None,
    obj: str = 'podium',
) -> str:
    """讲台样式。`obj` 必须和控件 setObjectName 一致——选择器对不上时
Qt 会静默丢弃整条规则（全屏讲台曾经因此没有底色也没有边框）。"""
    bd = PODIUM_BORDER_STAGE if stage else PODIUM_BORDER
    style = 'solid' if stage else 'dashed'
    fs = int(round(font_px)) if font_px is not None else (40 if stage else 14)
    return (
        f'QLabel#{obj} {{ background: {GRAD_PODIUM}; border: 1px {style} {bd};'
        f' border-radius: {14 if stage else 12}px; color: {PODIUM_INK};'
        f' font-size: {fs}px; font-weight: 700; qproperty-alignment: AlignCenter; }}'
    )
