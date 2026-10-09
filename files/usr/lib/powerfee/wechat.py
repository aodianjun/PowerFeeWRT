#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""powerfee 的微信（ClawBot）配置助手。

给「服务 → 宿舍电量哨兵 → 微信推送」页面和 /cgi-bin/powerfee 端点共用，
也直接命令行可用：

    python3 /usr/lib/powerfee/wechat.py status            # JSON：服务/绑定/推送状态
    python3 /usr/lib/powerfee/wechat.py qr                # PNG 二进制写到 stdout
    python3 /usr/lib/powerfee/wechat.py qr --json         # JSON：base64 PNG + 哈希/年龄
    python3 /usr/lib/powerfee/wechat.py qr --out /tmp/q.png
    python3 /usr/lib/powerfee/wechat.py enable            # 一键写 UCI + commit + 发测试
    python3 /usr/lib/powerfee/wechat.py test              # 只发一条测试消息
    python3 /usr/lib/powerfee/wechat.py qr --from-file /tmp/log   # 调试：从日志文件解析
    python3 /usr/lib/powerfee/wechat.py qr --allow-stale  # 调试：旧的二维码也照出

设计要点
--------
* **两种服务形态都支持**：优先原生 OpenWrt 包（/usr/bin/weclawbot-api、
  /etc/init.d/weclawbot-api、配置 /etc/weclawbot/config/auth.json），
  没有原生包时回退 Docker 容器（weclawbot-api，配置 /opt/weclawbot/config/auth.json）。
  配置来源与日志来源都是「先探原生、没有再看 Docker」。
* **二维码**是服务打到 stdout 的字符画（半块字符 █▀▄，45 列，可能带 docker
  ISO 时间戳或 syslog 时间戳前缀）。这里把它还原成可扫的 PNG：解析半块字符 →
  自动判极性（找 7x7 定位图案）→ 裁掉外框 → 补 4 模块静区 → 放大 8 倍。
  解析逻辑移植自已实测过的 pf_probe/wechat_qr.py。
* **凭据不出门**：auth.json 里的 bot_token / api_token 只用于拼 UCI 与请求头，
  绝不写日志、绝不进返回值。
* 只依赖标准库（python3）。

公共部分（子进程 / UCI / 服务探测 / 推送状态 / ClawBot 启用）在 pfcommon.py，
与推送通道管理器 push.py（Server酱 / 企业微信 / 自定义 webhook）共用一份实现。

退出码：0 成功；1 业务失败（原因在 stderr / JSON 的 error 字段）；2 参数错误。
"""

import argparse
import base64
import calendar
import hashlib
import io
import os
import re
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pfcommon  # noqa: E402
from pfcommon import (  # noqa: E402
    NATIVE_AUTH, Service, clawbot_pick, clawbot_uci, last_push_line, notify_state,
    out_json, tcp_reachable,
)

pfcommon.TAG = "wechat"      # stderr 上的人话前缀保持 1.0.2 的样子
fail = pfcommon.fail

QR_CHARS = " \u2580\u2584\u2588"          # 空格 / ▀ / ▄ / █
SCALE = 8                                  # 每个模块放大成 8x8 像素
QUIET = 4                                  # 静区（模块数）
QR_MAX_AGE = 300                           # 秒：二维码超过这个岁数基本已过期
QR_MAX_TAIL = 40                           # 行：二维码块后面还有这么多行 => 不是"当前"那张

DOCKER_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s?")
SYSLOG_TS_RE = re.compile(
    r"^[A-Z][a-z]{2} [A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2} \d{4} ")
# syslog 正文前缀：facility.severity（如 user.notice）后跟 tag（如 weclawbot-api:）
SYSLOG_TAG_RE = re.compile(r"^(?:\S+ )?[\w./-]+(?:\[\d+\])?: ")


# ---- 二维码：日志字符画 -> PNG ----------------------------------------------

def _strip_prefix(line):
    """去掉 docker ISO 时间戳 / syslog 时间戳 / syslog tag，返回 (ts, 正文)。"""
    ts = None
    m = DOCKER_TS_RE.match(line)
    if m:
        ts = line[:m.end()].strip()
        line = line[m.end():]
    else:
        m = SYSLOG_TS_RE.match(line)
        if m:
            ts = line[:m.end()].strip()
            line = line[m.end():]
            m2 = SYSLOG_TAG_RE.match(line)
            if m2:
                line = line[m2.end():]
    return ts, line


def find_qr_blocks(text):
    """从日志文本里找出所有二维码字符画块，返回 [(lines, last_ts, tail_gap), ...]。

    tail_gap = 这一块结束后还有多少非空行 —— 用来判断"这张二维码是不是当前的"。
    """
    blocks, cur, cur_ts = [], [], None
    lines = text.splitlines()
    for idx, raw in enumerate(lines):
        ts, line = _strip_prefix(raw)
        if line and len(line) >= 20 and all(c in QR_CHARS for c in line):
            cur.append(line)
            if ts:
                cur_ts = ts
        else:
            if len(cur) >= 12:
                tail = sum(1 for l in lines[idx + 1:] if l.strip())
                blocks.append((cur, cur_ts, tail))
            cur, cur_ts = [], None
    if len(cur) >= 12:
        blocks.append((cur, cur_ts, 0))
    return blocks


def _ts_to_epoch(ts):
    """docker ISO(UTC) / syslog(本地) 时间戳 -> epoch；失败返回 None。"""
    if not ts:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", ts)
    if m:
        try:
            st = time.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")
            return calendar.timegm(st)          # docker -t 打的是 UTC
        except ValueError:
            return None
    try:
        st = time.strptime(ts, "%a %b %d %H:%M:%S %Y")
        return time.mktime(st)                  # logread 打的是本地时间
    except ValueError:
        return None


def to_matrix(lines):
    """半块字符 -> 模块矩阵（1 = 深色模块，先假设块字符是深色）。"""
    m = []
    width = min(len(l) for l in lines)
    for line in lines:
        top, bot = [], []
        for c in line[:width]:
            top.append(1 if c in "\u2588\u2580" else 0)
            bot.append(1 if c in "\u2588\u2584" else 0)
        m.append(top)
        m.append(bot)
    return m


def find_finders(m):
    """找 7x7 定位图案（外框实、内环空、中心实）。返回 [(row, col)]。"""
    h = len(m)
    w = len(m[0]) if h else 0
    found = []
    for r in range(max(0, h - 6)):
        for c in range(max(0, w - 6)):
            ok = True
            for dr in range(7):
                for dc in range(7):
                    border = dr in (0, 6) or dc in (0, 6)
                    ring = (dr in (1, 5) and 1 <= dc <= 5) or (dc in (1, 5) and 1 <= dr <= 5)
                    core = 2 <= dr <= 4 and 2 <= dc <= 4
                    want = 1 if (border or core) else 0
                    if ring:
                        want = 0
                    if m[r + dr][c + dc] != want:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                found.append((r, c))
    return found


def pick_orientation(m):
    """返回 (矩阵, 说明)：优先原样，找不到定位图案就试反相。"""
    f = find_finders(m)
    if len(f) >= 2:
        return m, "原样"
    inv = [[1 - v for v in row] for row in m]
    f2 = find_finders(inv)
    if len(f2) >= 2:
        return inv, "反相"
    return None, "两种极性都没找到定位图案（%d / %d）" % (len(f), len(f2))


def crop_qr(m):
    """按定位图案裁出二维码区域（含内边界），返回 (矩阵, 边长模块数)。"""
    f = find_finders(m)
    uniq = []
    for r, c in f:
        if not any(abs(r - ur) <= 3 and abs(c - uc) <= 3 for ur, uc in uniq):
            uniq.append((r, c))
    if len(uniq) < 3:
        raise ValueError("只找到 %d 个定位图案，无法定位二维码区域" % len(uniq))
    rows = sorted(set(r for r, _ in uniq))
    top = rows[0]
    left = min(c for r, c in uniq if r == rows[0])
    right = max(c for r, c in uniq if r == rows[0])
    size = right - left + 7
    return [row[left:left + size] for row in m[top:top + size]], size


def write_png(matrix, scale=SCALE, quiet=QUIET):
    """模块矩阵 -> PNG 字节（1 位灰度，0=黑 255=白）。"""
    n = len(matrix)
    side = (n + quiet * 2) * scale
    rows = []
    for y in range(side):
        my = y // scale - quiet
        row = bytearray()
        for x in range(side):
            mx = x // scale - quiet
            dark = 0 <= my < n and 0 <= mx < n and matrix[my][mx] == 1
            row.append(0 if dark else 255)
        rows.append(bytes(row))
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    return png, side


def render_qr_from_log(text, mtime=None, source=""):
    """日志文本 -> (png, meta)；失败抛 ValueError。"""
    blocks = find_qr_blocks(text)
    if not blocks:
        raise ValueError("日志里没有二维码字符画（服务可能已登录，或还没开始打印）")
    lines, ts, tail_gap = blocks[-1]
    m = to_matrix(lines)
    oriented, how = pick_orientation(m)
    if oriented is None:
        raise ValueError("二维码解析失败：%s" % how)
    qr, size = crop_qr(oriented)
    png, px = write_png(qr)
    epoch = _ts_to_epoch(ts)
    if epoch is None and mtime:
        epoch = int(mtime)
    age = int(time.time() - epoch) if epoch else None
    stale = bool((age is not None and age > QR_MAX_AGE) or tail_gap > QR_MAX_TAIL)
    meta = {
        "ok": True,
        "modules": size,
        "px": px,
        "polarity": how,
        "source": source,
        "age": age,
        "tail_gap": tail_gap,
        "stale": stale,
        "hash": hashlib.md5(b"".join(bytes(r) for r in qr)).hexdigest()[:12],
        "bytes": len(png),
    }
    return png, meta


# ---- 状态 -------------------------------------------------------------------

def collect_status(from_file=None):
    svc = Service().detect()
    if from_file:
        svc.mode = "native" if os.path.exists(NATIVE_AUTH) else svc.mode
    bots = pfcommon.read_auth(svc)
    bot_id, bot = pfcommon.pick_bot(bots)
    context_ready = bool(bot and (bot.get("context_token") or "").strip())
    api_token_set = bool(bot and (bot.get("api_token") or "").strip())
    ns = notify_state()

    expect_url = ""
    if bot_id:
        expect_url = "http://127.0.0.1:%d/bots/%s/messages" % (svc.port, bot_id)
    url_ok = bool(ns["url"]) and bool(expect_url) and ns["url"].strip() == expect_url

    if from_file:
        text, source, mtime = io.open(from_file, encoding="utf-8", errors="replace").read(), from_file, os.path.getmtime(from_file)
    else:
        text, source, mtime = svc.log_text()
    qr_meta = None
    if text:
        try:
            _, meta = render_qr_from_log(text, mtime, source)
            qr_meta = meta
        except ValueError:
            qr_meta = None

    installed = svc.mode != "none" or bool(bots) or bool(svc.auth_path)
    st = {
        "installed": installed,
        "mode": svc.mode,
        "running": svc.running,
        "bound": bool(bot_id),
        "bot_id": bot_id or "",
        "bot_count": len(bots),
        "context_ready": context_ready,
        "api_token_set": api_token_set,
        "api_port": svc.port,
        "api_reachable": tcp_reachable(svc.port) if installed else False,
        "config_path": svc.auth_path,
        "log_source": source,
        "qr_available": bool(qr_meta) and not qr_meta.get("stale"),
        "qr_stale": bool(qr_meta) and bool(qr_meta.get("stale")),
        "qr_hash": (qr_meta or {}).get("hash", ""),
        "qr_age": (qr_meta or {}).get("age"),
        "notify_enabled": ns["enabled"],
        "notify_url": ns["url"],
        "notify_url_ok": url_ok,
        "notify_token_set": ns["token_set"],
        "notify_method": ns["method"],
        "expect_url": expect_url,
        "last_push": last_push_line(),
        "ready": bool(installed and svc.running and bot_id and context_ready),
    }
    return st


# ---- 子命令 -----------------------------------------------------------------

def cmd_status(args):
    return out_json(collect_status(args.from_file))


def cmd_qr(args):
    if args.from_file:
        try:
            with io.open(args.from_file, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            mtime = os.path.getmtime(args.from_file)
        except IOError as exc:
            return fail("读取 %s 失败：%s" % (args.from_file, exc))
        source = args.from_file
    else:
        svc = Service().detect()
        text, source, mtime = svc.log_text()
    if not text:
        return fail("拿不到服务日志（原生日志文件 / logread / docker logs 都没有内容）")
    try:
        png, meta = render_qr_from_log(text, mtime, source)
    except ValueError as exc:
        return fail(str(exc))
    if meta.get("stale") and not args.allow_stale:
        # 日志里保留着上一轮登录的二维码（多半已经过期或已经扫过），
        # 直接吐给用户会白扫一次 —— 说明清楚，并告诉他怎么重新拿一张。
        age = meta.get("age")
        when = ("约 %d 分钟前" % (age // 60)) if age else "更早"
        return fail("日志里最新的二维码是%s打印的，早就过期了（服务可能已经登录过）"
                    "；要重新扫码请重启微信服务，让它重新打印一张" % when,
                    stale=True, age=age, hash=meta.get("hash"))
    if args.out:
        try:
            with open(args.out, "wb") as fh:
                fh.write(png)
        except IOError as exc:
            return fail("写 %s 失败：%s" % (args.out, exc))
    if args.json or args.out:
        obj = dict(meta)
        if args.json:
            obj["png_b64"] = base64.b64encode(png).decode("ascii")
        return out_json(obj)
    sys.stdout.flush()
    sys.stdout.buffer.write(png)
    sys.stdout.buffer.flush()
    return 0


def cmd_enable(args):
    svc, bot_id, bot, err = clawbot_pick(args.bot)
    if err:
        return fail(err, bot_id=bot_id or "")
    url, pairs = clawbot_uci(bot_id, (bot.get("api_token") or "").strip(), svc.port)
    body = '{"text":"{text}"}'
    steps = []
    if args.dry_run:
        # 只检查：不写配置、不发消息（返回值里同样不含令牌）
        return out_json({
            "ok": True,
            "dry_run": True,
            "bot_id": bot_id,
            "url": url,
            "steps": ["仅检查，没有写 UCI，也没有发测试消息"],
            "would_set": {
                "powerfee.notify.enabled": "1",
                "powerfee.notify.url": url,
                "powerfee.notify.method": "POST",
                "powerfee.notify.content_type": "application/json",
                "powerfee.notify.body": body,
                "powerfee.notify.token_header": "Authorization",
                "powerfee.notify.token": "Bearer <api_token>（已隐藏）",
                "powerfee.notify.timeout": "15",
            },
            "message": "dry-run：配置未改动",
        })

    ok, uerr = pfcommon.uci_apply([("powerfee.notify", "notify")] + pairs)
    if not ok:
        return fail(uerr, bot_id=bot_id)
    steps.append("UCI 已写入并 commit")

    ns = notify_state()
    ok_url = ns["url"].strip() == url
    if not ok_url:
        return fail("配置写入后校验不一致：notify.url=%s，期望 %s" % (ns["url"], url),
                    bot_id=bot_id, url=url)

    rc, msg = pfcommon.notify_test()
    pushed = rc == 0
    steps.append("测试消息已发送" if pushed else "测试消息发送失败")
    res = {
        "ok": pushed,
        "bot_id": bot_id,
        "url": url,
        "steps": steps,
        "message": msg[:300],
        "last_push": last_push_line(),
    }
    if not pushed:
        res["error"] = msg[:300] or "powerfee notify-test 失败（详见 powerfee log）"
    return out_json(res, 0 if pushed else 1)


def cmd_test(args):
    rc, msg = pfcommon.notify_test()
    return out_json({
        "ok": rc == 0,
        "message": msg[:300] or ("powerfee notify-test 退出码 %d" % rc),
        "last_push": last_push_line(),
    }, 0 if rc == 0 else 1)


# ---- 入口 -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="powerfee wechat helper")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("status", help="输出 JSON 状态")
    p.add_argument("--from-file", help="(调试) 从本地日志文件读二维码，不探测服务")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("qr", help="输出二维码 PNG（--json 时输出 base64 JSON）")
    p.add_argument("--json", action="store_true", help="输出 JSON（含 png_b64）")
    p.add_argument("--out", help="把 PNG 写到文件")
    p.add_argument("--allow-stale", action="store_true",
                   help="即使二维码看起来已过期（登录后日志里还留着旧图）也照样输出")
    p.add_argument("--from-file", help="(调试) 从本地日志文件读二维码，不探测服务")
    p.set_defaults(func=cmd_qr)

    p = sub.add_parser("enable", help="一键配置推送并发送测试消息")
    p.add_argument("--bot", help="指定 bot_id（默认自动选已激活的那个）")
    p.add_argument("--dry-run", action="store_true", help="只检查，不写配置")
    p.set_defaults(func=cmd_enable)

    p = sub.add_parser("test", help="发送一条测试推送")
    p.set_defaults(func=cmd_test)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
