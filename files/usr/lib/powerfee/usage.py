#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""powerfee 的逐日用电量助手（纯标准库；不碰网络、不读 UCI、不发邮件）。

主程序（busybox ash 的 /usr/bin/powerfee）负责取数（curl，请求头纪律与
fetch_rooms 一致）与缓存决策；本助手只做数据加工与绘图。子命令：

    parse     接口原始响应（已由 jsonfilter 按 api.daily_path 拆成每行一个对象）
              -> 规整 JSON：逐日 used/total、单位、历史月「月末缺日」标记
    fix       历史月最后一天缺失时，用下个月首日数据恢复
              （公式见下）；下个月拿不到就把该日标成缺值（渲染器会断开）
    estimate  从本地采样 history.csv 估算每日用量（接口不可用时的兜底数据源）
    render    规整 JSON -> 文本图 / PNG / HTML / JSON 载荷（调用 chart.py）
    today     从本地采样取「今天到目前」的用量（定时报告用）

规整 JSON（也就是缓存文件 <state_dir>/usage.<房间id>.<YYYY-MM>.json 的内容）::

    {"ok":true,"source":"api","unit":"度","month":"2026-10","days":[
        {"date":"2026-10-01","used":30.3,"total":12745.73}, ...]}

* ``used`` 为 null = 该日缺数据（画图时断开，不猜不补）
* ``total`` = 接口的终身累计（单调递增），跨月补齐公式要用
* estimate 来源的日另有 ``"incomplete":true``（当天采样有空洞）

跨月补齐公式（学校接口的已知坑：每个历史月的最后一天不返回）::

    used(M月最后一天) = total(M+1月1日) - total(M月最后返回日) - used(M+1月1日)

估算口径（estimate / today，与「按采样次数平均」划清界限）:
* 每段相邻采样的用量（前一点余额 - 后一点余额）归给**后一点所在的那一天**；
  跨午夜的间隔归给午夜后的那一天 —— 也就是拿「午夜前最后一点」当基准、
  「午夜后第一点」当锚点。
* 余额**上升**（充值）不参与当日累加（否则那天会算出负用量）。
* 采样间隔 > 2×interval 的日子标 ``incomplete``（当天数据有洞，别当成整天看）。

退出码：0 成功；1 数据问题（stderr 说明原因）。stdout 的状态行是给 shell 读的
单行 ``key=value``（形如 ``status=ok days=30 last_missing=1``），不含空格值。
"""

import argparse
import calendar
import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import chart
except Exception as exc:  # pragma: no cover - 缺 chart.py 时给一句明白话
    sys.stderr.write("缺少绘图模块 chart.py（应与 usage.py 同目录）：%s\n" % exc)
    sys.exit(1)


# ---- 基础工具 ---------------------------------------------------------------

def _die(msg, rc=1):
    sys.stderr.write("%s\n" % msg)
    sys.exit(rc)


def _num(value):
    """任意输入 -> float 或 None（空串/非数字/NaN/inf 一律当缺值）。"""
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


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except Exception as exc:
        sys.stderr.write("读不了/解析不了 JSON（%s）：%s\n" % (path, exc))
        return None


def _dump(obj, path):
    """规整 JSON 落盘（紧凑、UTF-8、原子替换）。"""
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _iter_objects(path):
    """逐行读 JSONL（jsonfilter 对 [*] 路径的输出）；整行是数组时展开。

    容忍空行与坏行（接口偶尔会塞点怪东西；坏行跳过比整体失败强）。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, dict):
                            yield item
                elif isinstance(obj, dict):
                    yield obj
    except OSError as exc:
        sys.stderr.write("读不了逐日数据文件（%s）：%s\n" % (path, exc))


def _find_string(doc, key, depth=0):
    """在任意层级里找 key 的第一个非空字符串值（找单位字段用）。"""
    if depth > 8:
        return ""
    if isinstance(doc, dict):
        v = doc.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        for item in doc.values():
            got = _find_string(item, key, depth + 1)
            if got:
                return got
    elif isinstance(doc, list):
        for item in doc:
            got = _find_string(item, key, depth + 1)
            if got:
                return got
    return ""


def _month_last_day(month):
    """'YYYY-MM' -> 'YYYY-MM-DD'（该月最后一天）。"""
    try:
        y, m = month.split("-")
        d = calendar.monthrange(int(y), int(m))[1]
    except Exception:
        return ""
    return "%s-%02d" % (month, d)


def _day_before(day):
    try:
        d = datetime.date.fromisoformat(day)
    except Exception:
        return ""
    return (d - datetime.timedelta(days=1)).isoformat()


def _round2(v):
    return round(v + 0.0, 2) if v is not None else None


def _days_of(doc):
    """规整 JSON -> 按日期排序的 days 列表。"""
    days = doc.get("days") if isinstance(doc, dict) else None
    if not isinstance(days, list):
        return []
    out = []
    for item in days:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "").strip()
        if not date:
            continue
        rec = {"date": date, "used": _num(item.get("used"))}
        if item.get("total") is not None:
            rec["total"] = _num(item.get("total"))
        if item.get("incomplete"):
            rec["incomplete"] = True
        out.append(rec)
    out.sort(key=lambda r: r["date"])
    # 同一天重复出现时保留最后一条（后写的更新）
    dedup = {}
    for rec in out:
        dedup[rec["date"]] = rec
    return [dedup[k] for k in sorted(dedup)]


def _status(**kv):
    parts = []
    for k in sorted(kv):
        v = kv[k]
        if isinstance(v, bool):
            v = 1 if v else 0
        parts.append("%s=%s" % (k, v))
    sys.stdout.write(" ".join(parts) + "\n")


# ---- parse：接口原始响应 -> 规整 JSON ----------------------------------------

def cmd_parse(args):
    objs = list(_iter_objects(args.days_file))
    if not objs:
        sys.stderr.write("逐日数据为空（api.daily_path 是否指向每日明细数组？）\n")
        return 1

    days = []
    unit = ""
    for obj in objs:
        date = str(obj.get(args.date_field) or "").strip()
        if not date:
            continue
        used = _num(obj.get(args.used_field))
        total = _num(obj.get(args.total_field))
        rec = {"date": date, "used": used}
        if total is not None:
            rec["total"] = total
        days.append(rec)
        if not unit:
            unit = _find_string(obj, args.unit_field) if args.unit_field else ""
    if not days:
        sys.stderr.write("逐日数据里没有可用日期（date_field=%s 对不对？）\n" % args.date_field)
        return 1
    days.sort(key=lambda r: r["date"])
    dedup = {}
    for rec in days:
        dedup[rec["date"]] = rec
    days = [dedup[k] for k in sorted(dedup)]

    # 单位：逐日对象里没有就去整份原始响应里找（如 obj.dailyUsedUnit），
    # 再拿不到用 api.unit 兜底（--unit-fallback）
    if not unit and args.unit_field and args.raw_file:
        raw = _load_json(args.raw_file)
        if raw is not None:
            unit = _find_string(raw, args.unit_field)
    if not unit:
        unit = args.unit_fallback or ""

    doc = {"ok": True, "source": "api", "unit": unit, "days": days}
    last_missing = False
    if args.month:
        doc["month"] = args.month
        expect = _month_last_day(args.month)
        doc["expected_last"] = expect
        last_missing = bool(expect) and days[-1]["date"] != expect
        if last_missing:
            doc["last_missing"] = True
    _dump(doc, args.out)
    _status(status="ok", days=len(days), last_missing=last_missing,
            first_date=days[0]["date"], last_date=days[-1]["date"],
            unit=unit or "-")
    return 0


# ---- fix：历史月最后一天缺失 -> 用下个月首日恢复 -----------------------------

def cmd_fix(args):
    doc = _load_json(args.src)
    if doc is None:
        return 1
    days = _days_of(doc)
    expect = _month_last_day(args.month)
    if not expect:
        sys.stderr.write("月份格式不对：%s（要 YYYY-MM）\n" % args.month)
        return 1
    if not days or days[-1]["date"] == expect:
        _dump(doc, args.out)
        _status(status="ok", filled=False, last=days[-1]["date"] if days else "-")
        return 0

    last = days[-1]
    filled = False
    rec = {"date": expect, "used": None, "missing": True}
    # 恢复公式要求「M 月最后返回日」正好是 expect 的前一天，且下个月首日数据齐全；
    # 任何一条不满足都宁可标缺值（渲染器会断开），不瞎补。
    if args.next_file and last["date"] == _day_before(expect):
        nxt = _days_of(_load_json(args.next_file) or {})
        first = nxt[0] if nxt else None
        if (first and first.get("date") == _next_month_first(expect)
                and first.get("total") is not None and first.get("used") is not None
                and last.get("total") is not None):
            used = first["total"] - last["total"] - first["used"]
            if used >= 0:
                rec = {"date": expect, "used": _round2(used), "total": first["total"],
                       "filled": True}
                filled = True
    days.append(rec)
    out = dict(doc)
    out["days"] = days
    out["last_missing"] = not filled
    if filled:
        out.pop("last_missing", None)
    _dump(out, args.out)
    _status(status="ok", filled=filled, last=expect)
    return 0


def _next_month_first(day):
    """expect 的下一天（下个月 1 日）：'YYYY-MM-31' -> 'YYYY-MM+1-01'。"""
    try:
        d = datetime.date.fromisoformat(day) + datetime.timedelta(days=1)
    except Exception:
        return ""
    return d.isoformat()


# ---- estimate：从本地采样估算每日用量 ---------------------------------------

def _estimate_days(history, interval):
    """history.csv（epoch,balance 每行一条）-> {date: (used, incomplete)}。

    口径见模块开头：每段间隔的用量归给后一点所在的那一天；余额上升（充值）
    不计入；间隔 > 2×interval 的那天标 incomplete。
    """
    samples = []
    try:
        with open(history, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                parts = raw.strip().split(",")
                if len(parts) < 2:
                    continue
                try:
                    t = int(float(parts[0]))
                    b = float(parts[1])
                except ValueError:
                    continue
                samples.append((t, b))
    except OSError:
        return {}
    if len(samples) < 2:
        return {}
    samples.sort(key=lambda x: x[0])

    gap_limit = max(1, int(interval)) * 2
    acc = {}        # date -> [used, max_gap]
    for i in range(1, len(samples)):
        t0, b0 = samples[i - 1]
        t1, b1 = samples[i]
        d1 = datetime.datetime.fromtimestamp(t1).date().isoformat()
        used, gap = acc.get(d1, (0.0, 0))
        gap = max(gap, t1 - t0)
        if b0 > b1:
            used += b0 - b1          # 余额下降 = 正常用电
        # b1 >= b0：充值（或零变化）—— 不参与当日累加
        acc[d1] = (used, gap)

    first_day = datetime.datetime.fromtimestamp(samples[0][0]).date().isoformat()
    last_day = datetime.datetime.fromtimestamp(samples[-1][0]).date().isoformat()
    days = {}
    day = datetime.date.fromisoformat(first_day)
    end = datetime.date.fromisoformat(last_day)
    while day <= end:
        key = day.isoformat()
        if key in acc:
            used, gap = acc[key]
            days[key] = (_round2(used), gap > gap_limit)
        else:
            days[key] = (None, True)
        day += datetime.timedelta(days=1)
    # 第一天没有「前一点」当基准：当天首个采样晚于 2×interval 才算有洞
    if samples[0][0] - datetime.datetime.combine(
            datetime.date.fromisoformat(first_day), datetime.time.min).timestamp() > gap_limit:
        used, _ = days.get(first_day, (None, True))
        days[first_day] = (used, True)
    return days


def cmd_estimate(args):
    days_map = _estimate_days(args.history, args.interval)
    if not days_map:
        sys.stderr.write("本地采样不足（%s 里没有两个以上的有效点）\n" % args.history)
        return 1
    days = []
    for date in sorted(days_map):
        used, incomplete = days_map[date]
        rec = {"date": date, "used": used}
        if incomplete:
            rec["incomplete"] = True
        days.append(rec)
    _dump({"ok": True, "source": "estimate", "unit": args.unit or "",
           "days": days}, args.out)
    _status(status="ok", days=len(days), first_date=days[0]["date"],
            last_date=days[-1]["date"])
    return 0


def cmd_today(args):
    """今天到目前的用量（本地采样口径；报告用）。"""
    days_map = _estimate_days(args.history, args.interval)
    today = args.date or datetime.date.today().isoformat()
    if today not in days_map or days_map[today][0] is None:
        _status(status="empty", used="-")
        return 0
    used, _ = days_map[today]
    _status(status="ok", used=("%.2f" % used))
    return 0


# ---- render：规整 JSON -> 图 -------------------------------------------------

def _merge_inputs(paths):
    """合并多个规整 JSON（按日期去重，后读的覆盖先读的）。"""
    merged = {}
    unit = ""
    source = ""
    for path in paths:
        doc = _load_json(path)
        if doc is None:
            continue
        if not unit and isinstance(doc, dict) and doc.get("unit"):
            unit = str(doc["unit"])
        if not source and isinstance(doc, dict) and doc.get("source"):
            source = str(doc["source"])
        for rec in _days_of(doc or {}):
            merged[rec["date"]] = rec
    return [merged[k] for k in sorted(merged)], unit, source


def _select_window(days, args):
    """按 --date / --month / --days 选窗口；--days 从「最后一个有数据的日期」往前数。

    窗口里没有数据的日期补 used=None（图上断开），而不是跳过。
    """
    if args.date:
        return [d for d in days if d["date"] == args.date], args.date, args.date
    if args.month:
        win = [d for d in days if d["date"].startswith(args.month + "-")]
        return win, (win[0]["date"] if win else ""), (win[-1]["date"] if win else "")
    if not days:
        return [], "", ""
    last = days[-1]["date"]
    if args.days and args.days > 0:
        end = datetime.date.fromisoformat(last)
        start = end - datetime.timedelta(days=args.days - 1)
        have = dict((d["date"], d) for d in days)
        win = []
        day = start
        while day <= end:
            key = day.isoformat()
            win.append(have.get(key) or {"date": key, "used": None})
            day += datetime.timedelta(days=1)
        return win, start.isoformat(), last
    return days, days[0]["date"], last


def _summary(days):
    vals = [d for d in days if d.get("used") is not None]
    total = sum(d["used"] for d in vals) if vals else None
    mx = max(vals, key=lambda d: d["used"]) if vals else None
    mn = min(vals, key=lambda d: d["used"]) if vals else None
    return {
        "total": _round2(total),
        "avg": _round2(total / len(vals)) if vals else None,
        "max": _round2(mx["used"]) if mx else None,
        "max_date": mx["date"] if mx else "",
        "min": _round2(mn["used"]) if mn else None,
        "min_date": mn["date"] if mn else "",
        "count": len(vals),
        "missing": len(days) - len(vals),
        "incomplete": sum(1 for d in days if d.get("incomplete")),
    }


def _f2(v):
    return "-" if v is None else "%.2f" % v


def _clip(s, width):
    """按显示宽度截断（借 chart 的估算；出问题就退回字符数截断）。"""
    try:
        return chart._trunc(s, width)
    except Exception:
        return s if len(s) <= width else s[:max(1, width - 2)] + ".."


def _short_summary(summary, unit, width):
    """摘要行：宽度小（微信）时给短版，避免折行。"""
    if summary["count"] == 0:
        return "（窗口内没有可用数据）"
    full = ("合计 %s %s ｜ 日均 %s %s ｜ 最高 %s（%s） ｜ 最低 %s（%s） ｜ %d 天%s"
            % (_f2(summary["total"]), unit, _f2(summary["avg"]), unit,
               _f2(summary["max"]), summary["max_date"][5:] or "-",
               _f2(summary["min"]), summary["min_date"][5:] or "-",
               summary["count"],
               ("（缺 %d 天）" % summary["missing"]) if summary["missing"] else ""))
    short = ("合计 %s %s · 日均 %s %s%s"
             % (_f2(summary["total"]), unit, _f2(summary["avg"]), unit,
                (" · 缺 %d 天" % summary["missing"]) if summary["missing"] else ""))
    return _clip(short if width < 40 else full, width)


def _source_cn(source):
    if source == "estimate":
        return "本地采样估算"
    if source == "api":
        return "学校接口逐日数据"
    return source or "未知来源"


def cmd_render(args):
    paths = list(args.input)
    if args.in_list:
        try:
            with open(args.in_list, "r", encoding="utf-8", errors="replace") as fh:
                paths.extend([ln.strip() for ln in fh if ln.strip()])
        except OSError as exc:
            sys.stderr.write("读不了输入清单（%s）：%s\n" % (args.in_list, exc))
            return 1
    days, unit, source = _merge_inputs(paths)
    if args.unit and not unit:
        # --unit 只当兜底：数据里带了单位（接口返回的 dailyUsedUnit）就用数据里的
        unit = args.unit
    if args.source:
        source = args.source
    unit = unit or "度"
    win, date_from, date_to = _select_window(days, args)
    points = [(d["date"][5:], d["used"]) for d in win]     # 图上标签用 MM-DD
    summary = _summary(win)

    if args.kind == "text":
        out = chart.render_text(points, width=args.width, height=args.height,
                                unit=unit, title=args.title)
        sys.stdout.write(out + "\n\n")
        sys.stdout.write(_short_summary(summary, unit, args.width) + "\n")
        sys.stdout.write("数据来源：%s\n" % _source_cn(source))
        return 0
    if args.kind == "html":
        sys.stdout.write(chart.render_html(points, width=args.width, height=args.height,
                                           unit=unit, title=args.title) + "\n")
        return 0
    if args.kind == "png":
        if not args.out:
            sys.stderr.write("kind=png 需要 --out <文件>\n")
            return 1
        data = chart.render_png(points, width=args.width, height=args.height,
                                unit=args.unit or unit, title=args.title)
        with open(args.out, "wb") as fh:
            fh.write(data)
        _status(status="ok", bytes=len(data), points=len(points))
        return 0
    if args.kind == "json":
        payload = {
            "ok": summary["count"] > 0,     # 窗口里一个可用点都没有 -> ok=false（机器人据此换话术）
            "source": source or "",
            "unit": unit,
            "empty": summary["count"] == 0,
            "from": date_from,
            "to": date_to,
            "days": [{"date": d["date"], "used": d.get("used"),
                      "incomplete": bool(d.get("incomplete"))} for d in win],
            "summary": summary,
            "text": chart.render_text(points, width=args.width, height=args.height,
                                      unit=unit, title=args.title),
            "summary_line": _short_summary(summary, unit, args.width),
            "source_text": _source_cn(source),
        }
        if args.png_base64:
            import base64
            payload["png_base64"] = base64.b64encode(
                chart.render_png(points, width=640, height=240,
                                 unit=args.png_unit or "kWh", title=args.png_title or "")
            ).decode("ascii")
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    sys.stderr.write("未知的 --kind：%s\n" % args.kind)
    return 1


# ---- 入口 -------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="powerfee usage helper")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("parse")
    p.add_argument("--days-file", required=True, help="jsonfilter 拆出的逐日对象（JSONL）")
    p.add_argument("--raw-file", default="", help="接口原始响应（找单位字段用）")
    p.add_argument("--out", required=True)
    p.add_argument("--month", default="", help="YYYY-MM；给了就算 last_missing")
    p.add_argument("--date-field", required=True)
    p.add_argument("--used-field", required=True)
    p.add_argument("--total-field", required=True)
    p.add_argument("--unit-field", default="")
    p.add_argument("--unit-fallback", default="")

    p = sub.add_parser("fix")
    p.add_argument("--month", required=True)
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--next", dest="next_file", default="")

    p = sub.add_parser("estimate")
    p.add_argument("--history", required=True)
    p.add_argument("--interval", type=int, default=1800)
    p.add_argument("--unit", default="")
    p.add_argument("--out", required=True)

    p = sub.add_parser("today")
    p.add_argument("--history", required=True)
    p.add_argument("--interval", type=int, default=1800)
    p.add_argument("--date", default="")

    p = sub.add_parser("render")
    p.add_argument("--kind", required=True, choices=["text", "png", "html", "json"])
    p.add_argument("--in", dest="input", action="append", default=[],
                   help="规整 JSON（可多次给，按日期合并）")
    p.add_argument("--in-list", dest="in_list", default="",
                   help="输入清单文件（每行一个路径；给 shell 用，避免路径空格问题）")
    p.add_argument("--days", type=int, default=0)
    p.add_argument("--month", default="")
    p.add_argument("--date", default="")
    p.add_argument("--width", type=int, default=48)
    p.add_argument("--height", type=int, default=10)
    p.add_argument("--title", default="")
    p.add_argument("--unit", default="")
    p.add_argument("--source", default="")
    p.add_argument("--out", default="")
    p.add_argument("--png-base64", action="store_true")
    p.add_argument("--png-unit", default="")
    p.add_argument("--png-title", default="")

    args = ap.parse_args(argv)
    if args.cmd == "parse":
        return cmd_parse(args)
    if args.cmd == "fix":
        return cmd_fix(args)
    if args.cmd == "estimate":
        return cmd_estimate(args)
    if args.cmd == "today":
        return cmd_today(args)
    if args.cmd == "render":
        if not args.input and not args.in_list:
            sys.stderr.write("render 需要 --in <规整 JSON> 或 --in-list <清单文件>\n")
            return 1
        return cmd_render(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
