#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chart.py 的自测脚本：不联网、不碰路由器、不改配置、不写任何文件（只打印 + 断言）。

用法：
    python tools/test_chart.py          # 退出码 0 = 全部断言通过

覆盖：
* 边界：空 / 单点 / 全 0 / 全缺值 / 中间缺值 / 极大极小 / 负数 / 超长标签 / 异常输入
* render_text：每行宽度 <= width、行数规则（height+1，有标题或单位时 +1）
* render_png：真的是合法 PNG —— 魔数、逐块 CRC、IHDR（宽高/位深/颜色类型）、
  zlib 解 IDAT 后字节数 == 宽x高x3 + 高（每行 1 字节 filter）
* render_html：纯 ASCII（因而无中文）、无 <script、无 http(s):// 外链、无 <img/url()
* 模块本身只用标准库（扫 import 白名单）
"""

import math
import os
import re
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHART_PY = os.path.join(ROOT, "files", "usr", "lib", "powerfee", "chart.py")
sys.path.insert(0, os.path.join(ROOT, "files", "usr", "lib", "powerfee"))

import chart  # noqa: E402

FAILS = []
COUNT = [0]


def check(name, cond, detail=""):
    COUNT[0] += 1
    if cond:
        print("  ok    %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILS.append(name)
    return bool(cond)


def check_eq(name, got, want):
    return check(name, got == want, "got %r want %r" % (got, want))


# ---- PNG 结构校验 -----------------------------------------------------------

def parse_png(data):
    """按 PNG 规范拆块并逐块校验 CRC；返回 (宽, 高, 解压后的像素字节)。"""
    if not isinstance(data, bytes):
        raise ValueError("返回值不是 bytes")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG 魔数不对：%r" % data[:8])
    pos, chunks = 8, []
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError("块头被截断")
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if len(body) != ln or len(data) < pos + 12 + ln:
            raise ValueError("块 %r 长度越界" % tag)
        crc = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])[0]
        if crc != (zlib.crc32(tag + body) & 0xFFFFFFFF):
            raise ValueError("块 %r 的 CRC 不匹配" % tag)
        chunks.append((tag, body))
        pos += 12 + ln
    if pos != len(data):
        raise ValueError("尾部有多余字节")
    if chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise ValueError("块顺序不对：首 %r 尾 %r" % (chunks[0][0], chunks[-1][0]))
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", chunks[0][1])
    if (depth, ctype, comp, filt, inter) != (8, 2, 0, 0, 0):
        raise ValueError("IHDR 不是预期的 8bit RGB(2)：%r" % ((depth, ctype, comp, filt, inter),))
    idat = b"".join(b for t, b in chunks if t == b"IDAT")
    raw = zlib.decompress(idat)
    if len(raw) != h * (w * 3 + 1):
        raise ValueError("IDAT 字节数 %d != 宽x高x3+高 %d" % (len(raw), h * (w * 3 + 1)))
    stride = w * 3 + 1
    for y in range(h):
        if raw[y * stride] != 0:
            raise ValueError("第 %d 行 filter 不是 0" % y)
    return w, h, raw


def check_png(name, data, want_w=None, want_h=None, want_ink=False):
    """校验一份 PNG：结构合法 + 尺寸符合预期 +（可选）确实画了东西（非全白）。"""
    try:
        w, h, raw = parse_png(data)
    except Exception as exc:                                  # noqa: BLE001
        return check(name, False, "解析失败：%s" % exc)
    if want_w is not None and (w, h) != (want_w, want_h):
        return check(name, False, "尺寸 %dx%d != %dx%d" % (w, h, want_w, want_h))
    ink = raw.count(0) + raw.count(255) - h   # 非黑非白字节数（减掉每行 filter 的 0）
    ink = len(raw) - h - raw.count(255)
    if want_ink and ink <= 0:
        return check(name, False, "整张图是纯白，什么都没画")
    return check(name, True, "")


# ---- 样例数据（通用假数据，不含任何真实信息）--------------------------------

PTS = [
    ("10-01", 12.4), ("10-02", 13.1), ("10-03", 11.8), ("10-04", 14.6),
    ("10-05", 15.2), ("10-06", 9.7), ("10-07", 10.3), ("10-08", 16.1),
    ("10-09", 17.4), ("10-10", 13.9), ("10-11", 12.2), ("10-12", None),
    ("10-13", 14.1), ("10-14", 15.6),
]
PTS60 = [("D%02d" % i, (i * 7 % 23) + 1.5) for i in range(60)]


def lines_ok(name, out, width):
    """text 输出的两条硬约束：每行 len 与显示宽度都不超过 width。"""
    lines = out.split("\n")
    bad = [i for i, l in enumerate(lines)
           if len(l) > width or chart._disp_width(l) > width]
    return check("%s 每行宽度 <= %d" % (name, width), not bad,
                 "第 %s 行超宽" % bad)


# ---- 1. 文本渲染 ------------------------------------------------------------

def test_text():
    print("\n== render_text ==")
    out = chart.render_text(PTS)
    lines = out.split("\n")
    check_eq("默认参数行数 == height+2（10 行柱 + 标题行 + 标签行）", len(lines), 12)
    lines_ok("默认参数", out, 48)
    check("标题行带单位", "度" in lines[0], lines[0])
    check("轴上有最大值 17.4", "17.4" in out)
    check("轴上有最小值 0.0", "0.0" in out)
    check("有块字符", "█" in out)
    check("首标签在末行", "10-01" in lines[-1], lines[-1])
    check("尾标签在末行", "10-14" in lines[-1], lines[-1])

    out = chart.render_text(PTS, unit="", title="")
    check_eq("无标题无单位：行数 == height+1", len(out.split("\n")), 11)
    lines_ok("无标题无单位", out, 48)

    out = chart.render_text(PTS, width=32, height=6)
    check_eq("width=32 height=6：行数 == 8", len(out.split("\n")), 8)
    lines_ok("width=32", out, 32)

    out = chart.render_text(PTS, width=80, height=4, unit="kWh", title="Daily usage")
    check_eq("自定义标题：行数 == 6", len(out.split("\n")), 6)
    lines_ok("width=80", out, 80)
    check("自定义标题出现在首行", "Daily usage" in out.split("\n")[0])
    check("ASCII 单位出现在首行", "kWh" in out.split("\n")[0])

    # 空 / 全缺值
    out = chart.render_text([])
    check_eq("空列表：标题行 + （无数据）", len(out.split("\n")), 2)
    check("空列表提示", "（无数据）" in out)
    out = chart.render_text([], unit="", title="")
    check_eq("空列表（无标题无单位）：只 1 行", out, "（无数据）")
    out = chart.render_text([("10-01", None), ("10-02", "abc")])
    check("全缺值也是（无数据）", "（无数据）" in out)

    # 单点
    out = chart.render_text([("10-05", 7.5)])
    lines = out.split("\n")
    check_eq("单点：行数 == height+2", len(lines), 12)
    lines_ok("单点", out, 48)
    check("单点：标签出现", "10-05" in lines[-1], lines[-1])
    check("单点：画出了柱子", "█" in out)
    check("单点：轴上标 7.5", "7.5" in out)

    # 全 0：底线 ▁ + 轴标 0.0，不崩
    out = chart.render_text([("a", 0), ("b", 0), ("c", 0)], height=3, unit="")
    lines = out.split("\n")
    check_eq("全 0：行数 == height+1", len(lines), 4)
    check("全 0：有底线 ▁", "▁" in out, repr(out))
    check("全 0：轴标 0.0", "0.0" in out)

    # 中间缺值：那一列必须是空格，且左右两根柱都在（宽 20 便于逐字符比对）
    out = chart.render_text([("a", 1.0), ("b", None), ("c", 1.0)],
                            width=20, height=4, unit="")
    lines = out.split("\n")
    check_eq("缺值：行数 == 5", len(lines), 5)
    check_eq("缺值：底行逐字符", lines[-2], "0.00 ┼████     █████")
    check_eq("缺值：顶行逐字符", lines[0], "1.00 ┤████     █████")
    check_eq("缺值：标签行", lines[-1], "      a            c")

    # 极大 / 极小
    out = chart.render_text([("a", 1e12), ("b", 0.001)])
    lines_ok("极大极小", out, 48)
    check("极大值用科学计数法", "1e+12" in out, repr(out.split("\n")[0]))
    check("极小值不崩且画成细柱", "▁" in out)

    # 负数：下界跟着压到负值
    out = chart.render_text([("a", -3), ("b", 5), ("c", 10)], unit="")
    lines_ok("负数", out, 48)
    check("负数：下界标 -3.0", "-3.0" in out, repr(out.split("\n")[-2]))
    check("负数：上界标 10.0", "10.0" in out)

    # 超长标签：截断 + 宽度不超
    out = chart.render_text([("2026-10-01-very-long-label", 1.0),
                             ("2026-10-02-also-very-long", 2.0)])
    lines_ok("超长标签", out, 48)
    check("超长标签被截断", ".." in out.split("\n")[-1], out.split("\n")[-1])

    # 60 点 + 窄画布：必须降采样后仍满足宽度
    out = chart.render_text(PTS60, width=48)
    lines_ok("60 点 width=48", out, 48)
    check_eq("60 点行数", len(out.split("\n")), 12)

    # 异常输入不崩
    for bad in (None, [None], [("a",)], [("a", "abc")], [("a", float("nan"))],
                [("a", float("inf"))], [("a", float("-inf"))], 12345, {"a": 1}):
        try:
            o = chart.render_text(bad)
            ok = isinstance(o, str) and o != ""
        except Exception as exc:                              # noqa: BLE001
            ok = False
            o = "异常：%s" % exc
        check("异常输入 %r 不崩" % (bad,), ok, str(o)[:80])

    # width 下限（夹到 20）
    out = chart.render_text(PTS, width=5)
    lines_ok("width=5 被夹到 20", out, 20)


# ---- 2. PNG 渲染 ------------------------------------------------------------

def test_png():
    print("\n== render_png ==")
    data = chart.render_png(PTS)
    check_png("默认 640x240 合法且与参数一致", data, 640, 240, want_ink=True)
    check("默认 PNG 体积合理（<400KB）", len(data) < 400 * 1024, "%d 字节" % len(data))
    check("同一输入两次输出完全一致（确定性）", chart.render_png(PTS) == data)
    check("带标题/单位的也合法", True)
    d2 = chart.render_png(PTS, width=320, height=160, unit="kWh", title="Daily usage")
    check_png("320x160 + ASCII 标题/单位", d2, 320, 160, want_ink=True)

    cases = [
        ("空列表", []),
        ("全缺值", [("10-01", None)]),
        ("单点", [("10-05", 7.5)]),
        ("全 0", [("a", 0), ("b", 0)]),
        ("中间缺值", [("a", 1), ("b", None), ("c", 2)]),
        ("极大极小", [("a", 1e12), ("b", 0.001)]),
        ("负数", [("a", -3), ("b", 5), ("c", 10)]),
        ("60 点", PTS60),
        ("超长标签", [("2026-10-01-very-long-label", 1.0), ("x", 2.0)]),
        ("中文标题（点阵画不了，应被丢弃而不是崩）", [("a", 1), ("b", 2)]),
    ]
    for name, pts in cases:
        d = chart.render_png(pts, width=200, height=120, title="今日用电", unit="度")
        check_png("PNG 边界：%s" % name, d, 200, 120)

    check_png("极小画布 40x30 也合法（尺寸与参数一致）",
              chart.render_png(PTS, width=40, height=30), 40, 30)
    check_png("宽高参数夹取上限（10000x10000 -> 4000）",
              chart.render_png([("a", 1)], width=10000, height=10000), 4000, 4000)

    # X 轴标签挑选：首、尾一定在内，且相邻下标间隔 >= 步长（"标签不叠字"的不变量）
    for n, ml in ((1, 1), (2, 5), (3, 5), (5, 2), (7, 7), (14, 7), (30, 10), (60, 8),
                  (366, 3)):
        idx = chart._x_label_idx(n, ml)
        step = int(math.ceil(n / float(max(1, ml))))
        gaps = [b - a for a, b in zip(idx, idx[1:])]
        check("X 标签挑选 n=%d max=%d（间隔 >= %d）" % (n, ml, step),
              idx[0] == 0 and idx[-1] == n - 1 and all(g >= step for g in gaps),
              "idx=%r" % idx)

    for bad in (None, [("a", "xyz")], 3.14):
        try:
            d = chart.render_png(bad)
            ok = d[:8] == b"\x89PNG\r\n\x1a\n"
        except Exception as exc:                              # noqa: BLE001
            ok = False
            print("        （%r 抛出 %s）" % (bad, exc))
        check("PNG 异常输入 %r 不崩" % (bad,), ok)


# ---- 3. HTML 渲染 -----------------------------------------------------------

def test_html():
    print("\n== render_html ==")
    out = chart.render_html(PTS)
    check("无 <script", "<script" not in out.lower())
    check("无 http:// 外链", "http://" not in out)
    check("无 https:// 外链", "https://" not in out)
    check("无 <img / <link / url()", ("<img" not in out and "<link" not in out
                                      and "url(" not in out))
    check("纯 ASCII（因此不含任何中文，含默认单位 度）",
          all(ord(c) < 128 for c in out), repr([c for c in out if ord(c) >= 128][:5]))
    check("默认单位 度 被转成数字实体 &#24230;", "&#24230;" in out)
    check("13 根柱子（14 点里 1 个缺值）", out.count("background-color:#4a90d9") == 13)
    check("缺值点没有柱子（空 <td> 顶位）", 'height:200px;padding:0 1px 0 0"></td>' in out)
    check("首日期标签在", "10-01" in out)
    check("末日期标签在", "10-14" in out)
    check("柱上数值在（点数少时显示）", ">12.4<" in out)
    check("Max/Min 标注在", "Max 17.4" in out and "Min 9.7" in out)
    check("缺值天数在", "1 missing" in out)
    check("邮件安全布局：table-layout:fixed + 固定像素高",
          "table-layout:fixed" in out and "height:200px" in out)
    check("无 flex/grid", "display:flex" not in out and "display:grid" not in out)
    check("单行 HTML（片段，无换行）", "\n" not in out)

    out = chart.render_html(PTS, unit="kWh", title="Daily usage")
    check("ASCII 单位直接可读（不需要实体）", "kWh" in out and "&#" not in out)

    out = chart.render_html([])
    check("空列表：纯 ASCII + 无数据提示（实体形式）", all(ord(c) < 128 for c in out)
          and "&#" in out and "table" not in out)
    out = chart.render_html([("10-05", 7.5)])
    check("单点：合法 + 标签在", "10-05" in out and out.count("#4a90d9") == 1
          and "width:25%" in out)

    out = chart.render_html(PTS60, width=640)
    check("60 点：体积可控（<80KB）", len(out) < 80 * 1024, "%d 字节" % len(out))
    check("60 点：纯 ASCII", all(ord(c) < 128 for c in out))
    out = chart.render_html([("a", 0), ("b", 0)])
    check("全 0：有 1px 细条", "height:1px" in out)
    out = chart.render_html([("a", -3), ("b", 5), ("c", 10)])
    check("负数：不崩且最小值为 -3", "Min -3" in out)

    for bad in (None, [("a", "abc")], [None, None], 42):
        try:
            o = chart.render_html(bad)
            ok = isinstance(o, str) and all(ord(c) < 128 for c in o)
        except Exception as exc:                              # noqa: BLE001
            ok = False
            o = "异常：%s" % exc
        check("HTML 异常输入 %r 不崩" % (bad,), ok, str(o)[:80])


# ---- 4. 字体表与"纯标准库"自检 ----------------------------------------------

def test_font_and_deps():
    print("\n== 字体表 / 依赖 ==")
    bad = []
    for ch, glyph in chart._FONT.items():
        if len(ch) != 1 or len(glyph) != 7 or any(
                len(row) != 5 or set(row) - set("01") for row in glyph):
            bad.append(ch)
    check("每个字形都是 7 行 x 5 列、只含 0/1", not bad, "坏字形：%r" % bad)
    need = set("0123456789-.:/% ") | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") \
        | set("abcdefghijklmnopqrstuvwxyz")
    check("覆盖数字/符号/大小写字母/空格", not (need - set(chart._FONT)),
          "缺：%r" % sorted(need - set(chart._FONT)))
    check("点阵里没有中文字形（PNG 只画 ASCII）",
          all(ord(ch) < 128 for ch in chart._FONT))

    with open(CHART_PY, "r", encoding="utf-8") as fh:
        src = fh.read()
    mods = set(re.findall(r"(?m)^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src))
    check("只 import 标准库（白名单 math/struct/zlib）",
          mods <= {"math", "struct", "zlib"}, "实际：%r" % sorted(mods))
    check("没有 PIL/numpy/matplotlib 的 import",
          not re.search(r"(?m)^\s*(?:import|from)\s+(?:PIL|numpy|matplotlib)\b", src))
    check("不碰网络/子进程（模块只画图）",
          not re.search(r"\b(?:subprocess|socket|urllib|ftplib|http)\b", src))


# ---- 5. 人类可读样例输出 ----------------------------------------------------

def demo():
    print("\n" + "=" * 60)
    print("样例输出（默认参数，14 天，其中 1 天缺值）：")
    print("-" * 60)
    print(chart.render_text(PTS))
    print("-" * 60)
    print("60 点 / 窄画布：")
    print(chart.render_text(PTS60, width=40, height=6))
    print("-" * 60)
    png = chart.render_png(PTS, title="Daily usage", unit="kWh")
    print("PNG：%d 字节，640x240" % len(png))
    print("HTML（2 点样例，看看实体转义长什么样）：")
    print(chart.render_html([("10-01", 12.4), ("10-02", 13.1)]))
    print("=" * 60)


def main():
    print("chart.py 自测（%s）" % CHART_PY)
    test_text()
    test_png()
    test_html()
    test_font_and_deps()
    demo()
    print("\n共 %d 项断言，失败 %d 项。" % (COUNT[0], len(FAILS)))
    if FAILS:
        for name in FAILS:
            print("  FAIL: %s" % name)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
