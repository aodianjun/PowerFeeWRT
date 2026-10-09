#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""powerfee 的绘图模块：把「每日用电量」画成 聊天文本 / PNG / HTML 三种形态。

只依赖 Python 标准库（math / struct / zlib）—— 目标环境是 OpenWrt 路由器上的
python3，没有 PIL / matplotlib / numpy，也不该有（装不下、也用不着）。
三个渲染函数共用同一份输入规整与取值口径，所以三张图"长"得对得上。

接口
----
    render_text(points, *, width=48, height=10, unit="度", title="") -> str
        多行文本柱状图（Unicode 块字符 ▁▂▃▄▅▆▇█），给微信/聊天回复用。
        行数 = height + 1；有标题或单位时最上面再 +1 行放标题行。
        每行显示宽度 <= width（非 ASCII 按 2 列保守估算 —— 宁可早截断，
        也不让微信里折行）。左轴标注最大值（顶行）与最小值（底行），
        末行是首、尾两个日期标签（挤不下时截断成 ".."）。
        空数据只回 "（无数据）"（有标题/单位时前面还有一行标题）。

    render_png(points, *, width=640, height=240, unit="度", title="") -> bytes
        完整 PNG 文件字节（8bit RGB；IHDR/IDAT/IEND 按规范手写，
        可直接 base64 内联进聊天消息，也可当邮件附件）。
        内容：浅色网格 + 柱状图 + Y 轴刻度数字 + X 轴日期标签 + 可选标题。
        文字用内置 5x7 点阵字体画；点阵只覆盖 ASCII，所以 PNG 里不会出现
        中文：标题/单位/标签里的非 ASCII 字符会被丢弃（请传 ASCII 标题或留空）。
        ASCII 单位会自动并进标题行：title="Daily" + unit="kWh" -> "Daily (kWh)"。

    render_html(points, *, width=640, height=200, unit="度", title="") -> str
        自包含 HTML 片段（全部内联样式，table 布局 + 固定像素高度；
        邮件客户端对 flex/grid 支持很差，所以故意不用）。不引外部 CSS/JS/图片。
        输出保持**纯 ASCII**：标题/单位/标签里的非 ASCII（比如默认单位 "度"）
        会转成 &#NNNN; 数字实体 —— 浏览器/邮件客户端照常显示中文，但不依赖
        文档编码声明，纯 ASCII 通道传输也不会烂码。
        点数多时 X 轴标签按组显示（首、尾一定在），避免挤成一团。

points 的取值口径（三个函数一致）
--------------------------------
* points 是 [(标签, 数值), ...]，按时间先后排列；标签通常是 "10-05" 这样的短日期。
* 缺值（None / 空串 / 非数字 / NaN / ±inf）：该点不画 —— 文本留空列、
  PNG 不画柱、HTML 不画柱；不连线、不断轴、不报错。
* 空列表 / 全是缺值：文本回 "（无数据）"；PNG 画空网格 + "no data"；HTML 回提示。
* 纵轴下界 = min(0, 最小值)：正常从 0 起画；出现负值时按比例画（不会出现负高度），
  文本/PNG 的下界标注就是那个负值；HTML 里把负值整体平移后按比例画。
* 全 0（极差为 0）：文本画一条底线 ▁、PNG/HTML 画 1px 细条 ——
  看得出"全平"而不是"没数据"。
* 极大值：刻度数字自动用紧凑格式（1.23e+12），不会把画布撑爆。
* 点数超过画布放得下的列数时按桶平均降采样（标签取桶内第一个；缺值不参与平均，
  整桶都缺就是断口）。
* width/height 会夹到合理区间（文本 width>=20、1<=height<=40；PNG 尺寸 8~4000；
  HTML width 160~1200、height 40~800），避免极端参数把输出撑爆。

这个模块只负责"画"：不读 UCI、不碰网络、不写文件。攒 points、把 PNG base64
进微信、把 HTML 塞进邮件，都是调用方的事。
"""

import math
import struct
import zlib

# ---- 颜色与字符常量 ---------------------------------------------------------

_BG = (255, 255, 255)          # 背景
_GRID = (233, 236, 240)        # 网格线（浅灰，不抢柱子）
_AXIS = (150, 155, 160)        # 坐标轴 / 零线
_TEXT = (90, 95, 100)          # 刻度与日期文字
_TITLE = (45, 50, 55)          # 标题文字
_BAR = (74, 144, 217)          # 柱子（与 HTML 里的 #4a90d9 保持一致）

_FULL = "█"                                        # 整格
_BLOCKS = "▁▂▃▄▅▆▇"                                # 1/8 ~ 7/8 格（第 8 档就是 █）
# 自己画的框图/块字符在等宽字体里都占 1 列；其余非 ASCII 一律按 2 列算。
_NARROW = frozenset("│┤┼└┴─" + _BLOCKS + _FULL)


# ---- 输入规整 ---------------------------------------------------------------

def _to_float(value):
    """任意输入 -> float 或 None（None/空串/非数字/NaN/±inf 一律当缺值）。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _norm_points(points):
    """把调用方的 points 规整成 [(label:str, value:float|None), ...]。

    不成对 / 为 None 的条目直接跳过：这个模块会在路由器上无人值守地跑，
    "宁可少画一根柱"也不能让渲染抛异常。
    """
    out = []
    if not points:
        return out
    try:
        items = list(points)
    except TypeError:
        return out
    for item in items:
        if item is None:
            continue
        try:
            label, value = item
        except (TypeError, ValueError):
            continue
        if label is None:
            label = ""
        out.append((str(label), _to_float(value)))
    return out


def _downsample(pts, max_cols):
    """点数超过 max_cols 时按桶平均降采样；缺值不参与平均，整桶缺值 -> 断口。"""
    n = len(pts)
    if n <= max_cols or max_cols < 1:
        return pts
    out = []
    for i in range(max_cols):
        lo = i * n // max_cols
        hi = (i + 1) * n // max_cols
        chunk = pts[lo:hi]
        vals = [v for _, v in chunk if v is not None]
        out.append((chunk[0][0], (sum(vals) / len(vals)) if vals else None))
    return out


def _clean_text(s):
    """标题/单位这类单行文字：把空白（含换行）压成单个空格，防止行数失控。"""
    return " ".join(str(s or "").split())


# ---- 宽度估算与截断（微信里显示宽度的近似）----------------------------------

def _disp_width(s):
    """估算显示宽度：框图/块字符按 1 列，其余非 ASCII 按 2 列。

    路由器上的 python3 是裁剪版，不保证有 unicodedata（拿不到
    east_asian_width），所以用"非 ASCII 一律算宽"的保守估法：
    估多了只是早一点截断，估少了会在微信里折行 —— 宁可早截断。
    """
    w = 0
    for ch in s:
        w += 1 if (ch in _NARROW or ord(ch) < 0x80) else 2
    return w


def _trunc(s, maxw, tail=".."):
    """按显示宽度截断；放不下时末尾接 tail（ASCII 的 ".."，宽度确定）。"""
    if maxw <= 0:
        return ""
    if _disp_width(s) <= maxw:
        return s
    if maxw < 2:
        return ""
    keep = maxw - len(tail)
    out = []
    w = 0
    for ch in s:
        cw = 1 if (ch in _NARROW or ord(ch) < 0x80) else 2
        if w + cw > keep:
            break
        out.append(ch)
        w += cw
    return "".join(out) + tail


def _clip_line(s, maxw):
    """兜底：保证一行不超过 maxw（正常布局本来就 <= maxw，这里是最后一道闸）。"""
    return s if _disp_width(s) <= maxw else _trunc(s, maxw)


def _fmt_num(v, span):
    """刻度/标注用的紧凑数字：按数值跨度选小数位，极大值退化成科学计数法。"""
    if v is None:
        return ""
    if v == 0:
        v = 0.0                       # 避免 "-0.0" 这种难看的东西
    a = abs(v)
    if a >= 1e6:
        return "%.3g" % v             # 1.234e+12 -> "1.23e+12"
    if span >= 20:
        d = 0
    elif span >= 2:
        d = 1
    elif span >= 0.2:
        d = 2
    elif span > 0:
        d = 3
    else:
        d = 1                         # 极差为 0（全平）：1 位小数够看
    return "%.*f" % (d, v)


# ---- 文本渲染（聊天回复）----------------------------------------------------

def _header_line(title, unit, width):
    """标题行：有标题就 "标题（单位）"，只有单位就 "单位：xx"，都没有就不出这行。"""
    t = _clean_text(title)
    u = _clean_text(unit)
    if not t and not u:
        return None
    if t and u:
        return _trunc("%s（%s）" % (t, u), width)
    return _trunc(t or ("单位：%s" % u), width)


def render_text(points, *, width=48, height=10, unit="度", title="") -> str:
    pts = _norm_points(points)
    width = max(20, int(width))
    height = max(1, min(40, int(height)))

    lines = []
    head = _header_line(title, unit, width)
    if head:
        lines.append(head)

    vals = [v for _, v in pts if v is not None]
    if not pts or not vals:
        lines.append("（无数据）")
        return "\n".join(lines)

    base = min(0.0, min(vals))        # 正常是 0；有负值就压到最小负值
    top = max(vals)
    rng = top - base
    hi_s = _fmt_num(top, rng)
    lo_s = _fmt_num(base, rng)
    gw = max(len(hi_s), len(lo_s))    # 左轴数字栏宽度（ASCII，len 就是列数）
    bar_cols = max(1, width - gw - 2)  # 再留 1 空格 + 1 个轴字符

    cols_pts = _downsample(pts, bar_cols)
    n = len(cols_pts)
    levels = height * 8               # 每行 8 档（▁▂▃▄▅▆▇█）
    if n == 1:
        # 单点：给一根窄柱，别糊成整块 —— 一天的数据本来就该"看着单薄"
        spans = [(0, min(bar_cols, 6))]
    else:
        spans = []
        for i in range(n):
            c0 = (i * bar_cols) // n
            c1 = ((i + 1) * bar_cols) // n
            if c1 <= c0:
                c1 = c0 + 1
            spans.append((c0, min(c1, bar_cols)))
    used = max(c1 for _, c1 in spans)  # 柱子实际用掉的列数（标签行对齐到它）

    lv_list = []
    for _, v in cols_pts:
        if v is None:
            lv_list.append(None)      # 缺值：整列留空（断口）
            continue
        if rng <= 0:
            lv_list.append(1)         # 全 0：给一条底线，看得出"全平"
            continue
        lv = int((v - base) / rng * levels + 0.5)
        if lv < 1:
            lv = 1                    # 有读数的点至少露 1/8 格（和缺值区分开）
        elif lv > levels:
            lv = levels
        lv_list.append(lv)

    for r in range(height):
        top_lv = (height - r) * 8
        bot_lv = top_lv - 8
        cells = [" "] * bar_cols
        for (c0, c1), lv in zip(spans, lv_list):
            if lv is None:
                continue
            if lv >= top_lv:
                ch = _FULL
            elif lv > bot_lv:
                ch = _BLOCKS[lv - bot_lv - 1]
            else:
                continue              # 这根柱够不到这一行
            for c in range(c0, c1):
                cells[c] = ch
        if r == 0:
            gutter = "%*s %s" % (gw, hi_s, "┤")
        elif r == height - 1:
            gutter = "%*s %s" % (gw, lo_s, "┼")
        else:
            gutter = " " * gw + " │"
        lines.append((gutter + "".join(cells)).rstrip())

    # X 轴标签：首、尾两个；挤不下就各分一半（截断），中间用空格顶开
    first, last = pts[0][0], pts[-1][0]
    avail = max(1, used)
    if first == last or avail < 4:
        part = _trunc(first, avail)
    else:
        half = max(1, (avail - 1) // 2)
        a = _trunc(first, half)
        b = _trunc(last, half)
        pad = avail - _disp_width(a) - _disp_width(b)
        part = a + " " * max(0, pad) + b
    lines.append(" " * (gw + 2) + part)

    return "\n".join(_clip_line(x, width) for x in lines)


# ---- 5x7 点阵字体 -----------------------------------------------------------
# 5 列 x 7 行，每行 5 个字符，"1" = 点亮的像素。只覆盖图表用得到的字符：
# 数字 0-9、- + . : / % ( ) ? 空格、大小写英文字母。
# PNG 里不画中文（点阵画不出来，也避免在只认 ASCII 的链路里出乱码），
# 表里没有的字符在渲染时直接跳过（见 _png_text）。
_FONT = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "?": ("01110", "10001", "00001", "00110", "00100", "00000", "00100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "a": ("00000", "00000", "01110", "00001", "01111", "10001", "01111"),
    "b": ("10000", "10000", "11110", "10001", "10001", "10001", "11110"),
    "c": ("00000", "00000", "01110", "10001", "10000", "10001", "01110"),
    "d": ("00001", "00001", "01111", "10001", "10001", "10001", "01111"),
    "e": ("00000", "00000", "01110", "10001", "11111", "10000", "01110"),
    "f": ("00110", "01001", "01000", "11100", "01000", "01000", "01000"),
    "g": ("00000", "01111", "10001", "10001", "01111", "00001", "01110"),
    "h": ("10000", "10000", "11110", "10001", "10001", "10001", "10001"),
    "i": ("00100", "00000", "01100", "00100", "00100", "00100", "01110"),
    "j": ("00010", "00000", "00110", "00010", "00010", "10010", "01100"),
    "k": ("10000", "10000", "10010", "10100", "11000", "10100", "10010"),
    "l": ("01100", "00100", "00100", "00100", "00100", "00100", "01110"),
    "m": ("00000", "00000", "11010", "10101", "10101", "10101", "10101"),
    "n": ("00000", "00000", "11110", "10001", "10001", "10001", "10001"),
    "o": ("00000", "00000", "01110", "10001", "10001", "10001", "01110"),
    "p": ("00000", "11110", "10001", "10001", "11110", "10000", "10000"),
    "q": ("00000", "01111", "10001", "10001", "01111", "00001", "00001"),
    "r": ("00000", "00000", "10110", "11001", "10000", "10000", "10000"),
    "s": ("00000", "00000", "01111", "10000", "01110", "00001", "11110"),
    "t": ("01000", "01000", "11100", "01000", "01000", "01001", "00110"),
    "u": ("00000", "00000", "10001", "10001", "10001", "10011", "01101"),
    "v": ("00000", "00000", "10001", "10001", "10001", "01010", "00100"),
    "w": ("00000", "00000", "10001", "10101", "10101", "10101", "01010"),
    "x": ("00000", "00000", "10001", "01010", "00100", "01010", "10001"),
    "y": ("00000", "10001", "10001", "10001", "01111", "00001", "01110"),
    "z": ("00000", "00000", "11111", "00010", "00100", "01000", "11111"),
}

_FONT_H = 7        # 字高（行）
_FONT_ADV = 6      # 字符步进：5 列字 + 1 列字距


def _png_text(s):
    """只留点阵画得出的字符（PNG 里不画中文：画不出来就不乱画）。"""
    return "".join(ch for ch in str(s or "") if ch in _FONT)


def _text_px(s, scale):
    """一行点阵文字的像素宽度（最后一列字距不算）。"""
    return (len(s) * _FONT_ADV - 1) * scale if s else 0


def _axis_label(v):
    """Y 轴刻度文字：%.4g 又短又准（12.5 / 0.25 / 1e+06 都表达得下）。"""
    if v == 0:
        return "0"
    return "%.4g" % v


def _x_label_idx(n, max_labels):
    """挑出要画 X 轴标签的点：先按步长均匀挑，再保证首、尾一定在。

    步长本身保证相邻标签的像素间距 >= 一个标签宽（调用方按这个选 max_labels），
    所以这里只要守住"相邻下标差 >= 步长"，标签就不会叠字 —— 末尾那个点
    离前一个标签不足一整步时，直接把它替换掉（而不是硬塞进去）。
    """
    step_i = int(math.ceil(n / float(max(1, max_labels))))
    idxs = list(range(0, n, step_i))
    if idxs and idxs[-1] != n - 1:
        if (n - 1) - idxs[-1] < step_i:
            idxs[-1] = n - 1
        else:
            idxs.append(n - 1)
    return idxs


# ---- 极简画布与 PNG 打包 ----------------------------------------------------

class _Canvas(object):
    """最小 RGB 画布：bytearray 存像素，所有绘制自带裁剪。

    自己撸一个而不引 PIL：路由器上只有标准库，而这里只需要
    矩形、横竖线、点阵文字三种原语。
    """

    def __init__(self, width, height, bg):
        self.w = width
        self.h = height
        self.buf = bytearray(bytes(bg) * (width * height))

    def rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.w - 1, x + w - 1)
        y1 = min(self.h - 1, y + h - 1)
        if x1 < x0 or y1 < y0:
            return
        row = bytes(color) * (x1 - x0 + 1)
        for yy in range(y0, y1 + 1):
            i = (yy * self.w + x0) * 3
            self.buf[i:i + len(row)] = row

    def hline(self, x0, x1, y, color):
        if x1 < x0:
            x0, x1 = x1, x0
        self.rect(x0, y, x1 - x0 + 1, 1, color)

    def vline(self, x, y0, y1, color):
        if y1 < y0:
            y0, y1 = y1, y0
        self.rect(x, y0, 1, y1 - y0 + 1, color)

    def text(self, x, y, s, color, scale):
        """5x7 点阵文字：每个"点"画成 scale x scale 的小方块。"""
        cx = x
        for ch in s:
            glyph = _FONT.get(ch)
            if glyph is not None:
                for ry, rowbits in enumerate(glyph):
                    for rx, bit in enumerate(rowbits):
                        if bit == "1":
                            self.rect(cx + rx * scale, y + ry * scale, scale, scale, color)
            cx += _FONT_ADV * scale

    def png(self):
        """打包成 PNG：每行前面 1 字节 filter（0=None），交给 zlib 压缩。"""
        stride = self.w * 3
        raw = bytearray()
        for y in range(self.h):
            raw.append(0)
            raw += self.buf[y * stride:(y + 1) * stride]
        return _png_bytes(self.w, self.h, raw)


def _png_bytes(width, height, raw):
    """按 PNG 规范打包：魔数 + IHDR + IDAT + IEND（8bit RGB，不折腾过滤器）。"""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    out += chunk(b"IEND", b"")
    return out


def _nice_axis(lo, hi, target):
    """把 [lo, hi] 扩到"好看"的刻度上（1/2/2.5/5 x 10^k），回 (lo, hi, ticks)。"""
    if not (hi > lo):
        hi = lo + 1.0
    span = hi - lo
    raw = span / max(1, target - 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    step = 10.0 * mag
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m * mag * (1 + 1e-9):
            step = m * mag
            break
    lo2 = math.floor(lo / step + 1e-9) * step
    hi2 = math.ceil(hi / step - 1e-9) * step
    if hi2 <= lo2:
        hi2 = lo2 + step
    cnt = int(round((hi2 - lo2) / step))
    ticks = [lo2 + i * step for i in range(cnt + 1)]
    return lo2, hi2, ticks


def render_png(points, *, width=640, height=240, unit="度", title="") -> bytes:
    pts = _norm_points(points)
    w = max(8, min(4000, int(width)))
    h = max(8, min(4000, int(height)))
    scale = 2 if w >= 320 else 1
    cv = _Canvas(w, h, _BG)

    # 标题行：ASCII 单位并进标题（点阵画不了中文单位，只能丢）
    cap = _clean_text(title)
    u = _png_text(_clean_text(unit))
    if u:
        cap = "%s (%s)" % (cap, u) if cap else u
    cap = _png_text(cap)
    top_y = 6 + ((_FONT_H * scale + 8) if cap else 0)

    vals = [v for _, v in pts if v is not None]
    has_data = bool(vals)
    if has_data:
        base = min(0.0, min(vals))
        top = max(0.0, max(vals))     # 强制把 0 包进轴里：负值也画得对
    else:
        base, top = 0.0, 1.0

    n_ticks = max(2, min(5, (h - top_y - 30) // (28 if scale == 2 else 18)))
    lo, hi, ticks = _nice_axis(base, top, n_ticks)
    ylabels = [_axis_label(t) for t in ticks]

    lbl_w = max(len(s) for s in ylabels) * _FONT_ADV * scale
    left = min(8 + lbl_w + 8, max(24, w // 2))
    right = max(left + 20, w - 8)
    bottom_y = max(top_y + 20, h - 8 - (_FONT_H * scale + 6))
    plot_w = right - left
    plot_h = bottom_y - top_y
    ax = max(0, left - 2)

    def y_of(v):
        y = bottom_y - int(round((v - lo) / (hi - lo) * plot_h))
        return max(top_y, min(bottom_y, y))

    # 网格线 + Y 轴刻度数字
    for t, s in zip(ticks, ylabels):
        y = y_of(t)
        cv.hline(left, right, y, _GRID)
        cv.text(max(2, ax - 6 - _text_px(s, scale)), y - (_FONT_H * scale) // 2,
                s, _TEXT, scale)

    x_labels = []
    if has_data:
        # 柱子：点数超过绘图区放得下的列数时按桶平均降采样
        max_cols = max(1, plot_w // 3)
        cols_pts = _downsample(pts, max_cols)
        n = len(cols_pts)
        slot = plot_w / float(n)
        barw = max(1, min(64, int(slot * 0.8)))   # 单点/两点时别糊成一整块
        y0 = y_of(0.0)
        for i, (_, v) in enumerate(cols_pts):
            if v is None:
                continue                          # 缺值：不画柱（断口）
            bx = left + int(i * slot + (slot - barw) / 2.0)
            yv = y_of(v)
            if yv <= y0:
                cv.rect(bx, yv, barw, y0 - yv + 1, _BAR)
            else:
                cv.rect(bx, y0, barw, yv - y0 + 1, _BAR)   # 负值向下画
        # 零线/底线：柱子画完再压上去，基线才干净
        cv.hline(left, right, y0, _AXIS)

        # X 轴日期标签：先按"每个标签占多少像素"决定隔几个画一个，
        # 首、尾一定在（末尾离前一个太近就把它替换掉，避免叠字）
        per_label = 5 * _FONT_ADV * scale + 14
        max_labels = max(1, plot_w // per_label)
        idxs = _x_label_idx(n, max_labels)
        step_i = max(1, (idxs[1] - idxs[0]) if len(idxs) > 1 else 1)
        chars_fit = max(3, int(step_i * slot / (_FONT_ADV * scale)) - 1)
        lab_y = bottom_y + 6
        for i in idxs:
            s = _trunc(_png_text(cols_pts[i][0]), chars_fit)
            if not s:
                continue
            x = left + int((i + 0.5) * slot) - _text_px(s, scale) // 2
            x = max(2, min(x, w - 2 - _text_px(s, scale)))
            x_labels.append((x, s))
        for x, s in x_labels:
            cv.text(x, lab_y, s, _TEXT, scale)
    else:
        # 空数据：画一张空网格，中间写 "no data"，总比抛异常强
        s = "no data"
        cv.text(left + max(0, (plot_w - _text_px(s, scale)) // 2),
                top_y + max(0, (plot_h - _FONT_H * scale) // 2), s, _TEXT, scale)

    # 坐标轴最后画，压在柱子/网格上面
    cv.vline(ax, top_y, bottom_y, _AXIS)
    cv.hline(ax, right, bottom_y, _AXIS)
    if cap:
        cv.text(max(2, (w - _text_px(cap, scale)) // 2), 6, cap, _TITLE, scale)

    return cv.png()


# ---- HTML 渲染（邮件正文）---------------------------------------------------

def _html_text(s):
    """HTML 片段里的一段文字：先转义，再把非 ASCII 换成 &#NNNN; 数字实体。

    邮件客户端对 UTF-8 声明/传输的兼容参差不齐；纯 ASCII 片段
    （中文用数字实体表达）在哪都能显示，也不会因为编码丢失变乱码。
    """
    out = []
    for ch in str(s or ""):
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif ch == '"':
            out.append("&quot;")
        elif ord(ch) < 0x80:
            out.append(ch)
        else:
            out.append("&#%d;" % ord(ch))
    return "".join(out)


def render_html(points, *, width=640, height=200, unit="度", title="") -> str:
    pts = _norm_points(points)
    w = max(160, min(1200, int(width)))
    h = max(40, min(800, int(height)))
    unit_s = _clean_text(unit)
    title_s = _clean_text(title)

    head = ""
    if title_s or unit_s:
        t = ("%s (%s)" % (title_s, unit_s)) if (title_s and unit_s) else (title_s or unit_s)
        head = ('<div style="font-size:13px;font-weight:bold;color:#2d3237;'
                'padding:0 0 6px 0">%s</div>' % _html_text(t))

    open_div = ('<div style="font-family:Arial,Helvetica,sans-serif;color:#333333;'
                'width:%dpx;max-width:100%%;font-size:12px;line-height:1.4">' % w)

    vals = [v for _, v in pts if v is not None]
    if not pts or not vals:
        return (open_div + head +
                '<div style="padding:10px 0;color:#6b7075">%s</div></div>'
                % _html_text("（无数据）"))

    base = min(0.0, min(vals))
    hi = max(vals)
    span = hi - base
    if span <= 0:
        span, hi = 1.0, base + 1.0     # 全 0 / 全平：给一个假高度，只为了能画出 1px 细条

    max_cols = max(1, min(180, w // 4))
    cols_pts = _downsample(pts, max_cols)
    n = len(cols_pts)

    show_vals = n <= max(2, w // 44)   # 点少时把每个数值顶在柱子上；点多了挤不下
    val_h = 13 if show_vals else 0
    area = max(1, h - val_h)
    one_w = "width:25%;" if n == 1 else ""   # 单点：别让一根柱铺满整行

    bar_cells = []
    for _, v in cols_pts:
        if v is None:
            bar_cells.append('<td style="vertical-align:bottom;height:%dpx;'
                             'padding:0 1px 0 0"></td>' % h)
            continue
        frac = (v - base) / span
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        bh = int(round(frac * area))
        if bh < 1:
            bh = 1                      # 有读数的点至少 1px（和缺值的空单元格区分开）
        cell = ""
        if show_vals:
            cell += ('<div style="height:12px;line-height:12px;font-size:9px;'
                     'text-align:center;color:#5a5f64;overflow:hidden">%s</div>'
                     % _html_text(_fmt_num(v, span)))
        cell += ('<div style="height:%dpx;background-color:#4a90d9;font-size:0;'
                 'line-height:0">&nbsp;</div>' % bh)
        bar_cells.append('<td style="vertical-align:bottom;height:%dpx;padding:0 1px 0 0;'
                         '%s">%s</td>' % (h, one_w, cell))

    # X 轴日期标签：按 colspan 分组（首、尾一定在；最后一组标"真·最后一天"）
    max_labels = max(2, w // 56)
    step_i = int(math.ceil(n / float(max_labels)))
    lab_cells = []
    i = 0
    while i < n:
        cnt = min(step_i, n - i)
        lab = cols_pts[n - 1][0] if (i + cnt >= n) else cols_pts[i][0]
        chars_fit = max(3, int(cnt * (w / float(n)) / 6.5))
        lab_cells.append('<td colspan="%d" style="font-size:10px;color:#6b7075;'
                         'text-align:center;padding:3px 0 0 0;overflow:hidden;'
                         'white-space:nowrap">%s</td>'
                         % (cnt, _html_text(_trunc(lab, chars_fit))))
        i += cnt

    miss = sum(1 for _, v in pts if v is None)
    cap = "Max %s / Min %s" % (_fmt_num(max(vals), span), _fmt_num(min(vals), span))
    if unit_s:
        cap += " / %s" % unit_s
    if miss:
        cap += " / %d missing" % miss

    return (open_div + head +
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;table-layout:fixed;width:100%%">'
            '<tr>%s</tr><tr>%s</tr></table>'
            '<div style="font-size:10px;color:#6b7075;padding:4px 0 0 0">%s</div></div>'
            % ("".join(bar_cells), "".join(lab_cells), _html_text(cap)))
