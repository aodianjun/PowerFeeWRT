#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
powerfee-chat —— 微信 ClawBot(iLink) 电量查询桥接

数据流:
    消息来源(可配置)  ->  正则解析 "Message from" 行  ->  关键词匹配
        ->  powerfee json brief/status  ->  POST /bots/<bot_id>/messages  ->  微信

消息来源 SOURCE 三种（解析逻辑完全共用）:
    logread  logread -f [-e <过滤模式>]                   （默认；原生服务日志进 syslog）
             注意：procd 用 /bin/sh -c 启动原生服务，它捕获的 stdout 在 syslog 里
             tag 是 "sh[pid]" 而不是 "weclawbot-api"，所以默认按消息内容
             过滤（LOGREAD_FILTER=Message from）而不是按 tag。
    logfile  tail -F -n 0 <日志文件>                    （服务把 stdout 落文件）
    docker   docker logs -f -t --since 1s <容器名>      （旧容器部署，回退用）

用法:
    powerfee-chat --daemon              常驻（由 /etc/init.d/powerfee-chat 拉起）
    powerfee-chat --once-line '<日志行>' 把一条日志行喂进真实管线（真查询/真发送）
    powerfee-chat --send-text '<文本>'   只发一条消息（诊断用，不经过关键词匹配）
    powerfee-chat --selftest            离线自测：假 API + 假 powerfee，断言关键行为
    powerfee-chat --check-config        打印生效配置（token 打码）

配置文件: /etc/powerfee-chat.conf（KEY=VALUE，见 powerfee-chat.conf.example）
"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

SERVICE = "powerfee-chat"
DEFAULT_CONF = "/etc/powerfee-chat.conf"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# 日志行样例（bot id / 用户 id 用占位）: [Bot: a1b2c3d4e5f6@im.bot | Message from o1234567890abcdefghijklmnop@im.wechat]: 1
# 用 search 而不是 match：docker logs -t / logread 的行首还有时间戳前缀。
MSG_RE = re.compile(
    r"\[Bot:\s*(?P<bot>[^|\]\s]+)\s*\|\s*Message from\s+(?P<from>[^\]]+?)\]\s*:?\s?(?P<text>.*)$"
)

# 行首是否带时间戳（docker -t 的 RFC3339 或 logread 的 syslog 格式）
TS_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})\s+"
    r"|(?:[A-Z][a-z]{2}\s+)?[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\s+\d{4})?\s+)"
)

# syslog 前缀: "Wed Oct  7 21:01:15 2026 user.notice weclawbot-api[9104]: <msg>"
SYSLOG_TAG_RE = re.compile(
    r"^(?:[A-Z][a-z]{2}\s+)?[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s+"
    r"\S+\s+(?P<tag>[^:\[\s]+)(?:\[\d+\])?:\s?"
)

HELP_TEXT = (
    "📖 电量查询指令\n"
    "· 电量 / 电费 / 余额 —— 查询剩余电量\n"
    "· 电量 详情 —— 详细状态（房间/档位/日均）\n"
    "· 曲线 / 用电 / 用量 —— 最近 7 天每日用电量曲线\n"
    "· 帮助 —— 显示本说明"
)

LEVEL_TXT = {
    "ok": "✅ 充足",
    "warn": "⚠️ 预警",
    "warning": "⚠️ 预警",
    "low": "🔴 偏低",
    "danger": "🔴 偏低",
    "error": "❌ 异常",
}

_CHILD = {"p": None}


# ---------------------------------------------------------------- logging ---
def log(msg, cfg=None):
    line = str(msg)
    if cfg is not None and getattr(cfg, "log_file", ""):
        try:
            with open(cfg.log_file, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except Exception:
            pass
    try:
        subprocess.run(["logger", "-t", SERVICE, line], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ----------------------------------------------------------------- config ---
class Config(object):
    def __init__(self, path=None):
        # 消息来源（默认原生服务经 syslog；docker 作为回退）
        self.source = "logread"           # logread | logfile | docker
        # logread -e 的过滤模式：原生服务的 stdout 由 procd 以 tag "sh[pid]" 记录，
        # 按消息内容过滤才抓得到 "[Bot: ... | Message from ...]" 行
        self.logread_filter = "Message from"
        self.container = "weclawbot-api"
        self.docker_log_args = "--since 1s"
        self.logfile = "/var/log/weclawbot.log"
        self.docker_bin = "/usr/bin/docker"
        self.tail_bin = "/usr/bin/tail"
        self.logread_bin = "/sbin/logread"
        # 发送侧
        self.api_base = "http://127.0.0.1:26322"
        self.bot_id = ""
        self.auth_json = "/opt/weclawbot/config/auth.json"
        self.api_token = ""
        # powerfee
        self.powerfee_bin = "/usr/bin/powerfee"
        # 关键词
        self.query_keywords = ["电量", "电费", "查电费", "余额", "剩余", "balance"]
        self.detail_keywords = ["详情", "全部", "detail", "all", "full"]
        self.help_keywords = ["帮助", "help", "菜单", "指令"]
        # 每日用电量曲线（1.2.0）：命中后调 powerfee json usage，回文本曲线
        self.usage_keywords = ["曲线", "用电", "用量", "趋势", "usage", "chart"]
        self.usage_days = 7
        # 行为
        self.throttle_seconds = 3.0
        self.dedup_seconds = 5.0
        self.ignore_from = []
        self.ignore_tags = ["powerfee-chat"]
        self.daily_push_time = ""
        self.log_file = ""
        if path and os.path.exists(path):
            self.load(path)
        self.token = self.api_token or self.read_token()

    # ---- conf 文件 ----
    def load(self, path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                s = raw.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip().upper()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if k == "SOURCE":
                    self.source = v.strip().lower()
                elif k == "CONTAINER":
                    self.container = v
                elif k == "DOCKER_LOG_ARGS":
                    self.docker_log_args = v
                elif k == "LOGFILE":
                    self.logfile = v
                elif k == "LOGREAD_FILTER":
                    self.logread_filter = v
                elif k == "LOGREAD_TAG":      # 兼容旧键名（旧值只匹配 tag，抓不到 sh[pid] 行）
                    self.logread_filter = v
                elif k == "DOCKER_BIN":
                    self.docker_bin = v
                elif k == "TAIL_BIN":
                    self.tail_bin = v
                elif k == "LOGREAD_BIN":
                    self.logread_bin = v
                elif k == "API_BASE":
                    self.api_base = v
                elif k == "BOT_ID":
                    self.bot_id = v
                elif k == "AUTH_JSON":
                    self.auth_json = v
                elif k == "API_TOKEN":
                    self.api_token = v
                elif k == "POWERFEE_BIN":
                    self.powerfee_bin = v
                elif k == "QUERY_KEYWORDS":
                    self.query_keywords = _split_list(v)
                elif k == "DETAIL_KEYWORDS":
                    self.detail_keywords = _split_list(v)
                elif k == "HELP_KEYWORDS":
                    self.help_keywords = _split_list(v)
                elif k == "USAGE_KEYWORDS":
                    self.usage_keywords = _split_list(v)
                elif k == "USAGE_DAYS":
                    self.usage_days = int(_to_float(v, 7))
                elif k == "THROTTLE_SECONDS":
                    self.throttle_seconds = _to_float(v, 3.0)
                elif k == "DEDUP_SECONDS":
                    self.dedup_seconds = _to_float(v, 5.0)
                elif k == "IGNORE_FROM":
                    self.ignore_from = _split_list(v)
                elif k == "IGNORE_TAGS":
                    self.ignore_tags = _split_list(v)
                elif k == "DAILY_PUSH_TIME":
                    self.daily_push_time = v
                elif k == "LOG_FILE":
                    self.log_file = v

    # ---- 从 auth.json 取 api_token（结构: bots/<bot_id>/api_token）----
    def read_token(self):
        try:
            with open(self.auth_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log("WARN 读取 auth.json 失败: %s" % e, self)
            return ""

        def search(obj):
            if isinstance(obj, dict):
                if self.bot_id and isinstance(obj.get("bots"), dict):
                    b = obj["bots"].get(self.bot_id)
                    if isinstance(b, dict) and isinstance(b.get("api_token"), str):
                        return b["api_token"]
                if isinstance(obj.get("api_token"), str):
                    return obj["api_token"]
                for v in obj.values():
                    r = search(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = search(v)
                    if r:
                        return r
            return None

        tok = search(data)
        if not tok:
            log("WARN auth.json 里没找到 api_token（%s）" % self.auth_json, self)
        return tok or ""


def _split_list(v):
    return [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]


def _to_float(v, default):
    try:
        return float(v)
    except Exception:
        return default


# ----------------------------------------------------------------- helpers ---
def _load_json(out):
    out = (out or "").strip()
    if not out:
        return None
    try:
        d = json.loads(out)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _fmt_ts(ts):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return str(ts)


def run_powerfee(cfg, args, runner=None):
    """返回 (rc, stdout, stderr)。runner 可注入（自测用）。"""
    cmd = [cfg.powerfee_bin] + list(args)
    if runner is not None:
        return runner(cmd)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return (p.returncode,
                p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))
    except Exception as e:
        return 127, "", repr(e)


def parse_brief(out, err=""):
    d = _load_json(out)
    if d is not None:
        if d.get("text"):
            return str(d["text"])
        if d.get("ok") is False:
            return "⚠️ 查询失败：%s" % (d.get("error") or d.get("msg") or "unknown")
    if (out or "").strip():
        return (out or "").strip().splitlines()[0][:300]
    return "⚠️ 查询失败：powerfee 无输出（%s）" % ((err or "").strip()[:120] or "rc!=0")


def format_detail(out, err=""):
    d = _load_json(out)
    if d is None:
        if (out or "").strip():
            return (out or "").strip()[:400]
        return "⚠️ 查询失败：powerfee 无输出（%s）" % ((err or "").strip()[:120] or "rc!=0")
    unit = d.get("unit") or "度"
    room = d.get("room_display") or " ".join(
        [x for x in [d.get("building"), d.get("room")] if x]) or "未知房间"
    lines = ["⚡ 电量详情 · %s" % room]
    l2 = "💰 剩余 %s %s" % (d.get("balance"), unit)
    if d.get("threshold") is not None:
        l2 += "（阈值 %s %s）" % (d.get("threshold"), unit)
    lines.append(l2)
    lvl = str(d.get("level") or "")
    l3 = "📊 状态 %s" % LEVEL_TXT.get(lvl.lower(), lvl or "—")
    if d.get("daily") is not None:
        l3 += " ｜ 日均 %s %s" % (d.get("daily"), unit)
    if d.get("days_left") is not None:
        l3 += " ｜ 约可用 %s 天" % d.get("days_left")
    lines.append(l3)
    if d.get("last_ok_at"):
        lines.append("🕒 上次成功 %s ｜ 累计查询 %s 次" % (_fmt_ts(d.get("last_ok_at")),
                                                          d.get("poll_count", "?")))
    if d.get("last_error"):
        lines.append("⚠️ 最近错误：%s" % str(d["last_error"])[:60])
    return "\n".join(lines[:6])


# ------------------------------------------------------------ send / match ---
class Sender(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.history = []

    def send(self, bot_id, text):
        bot_id = bot_id or self.cfg.bot_id
        if not bot_id:
            log("发送失败: 没有 bot_id", self.cfg)
            return False
        if not self.cfg.token:
            log("发送失败: 没有 api_token", self.cfg)
            return False
        url = "%s/bots/%s/messages" % (self.cfg.api_base.rstrip("/"), bot_id)
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer %s" % self.cfg.token)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read().decode("utf-8", "replace")
            self.history.append((time.time(), bot_id, text))
            log("发送成功 bot=%s resp=%s" % (bot_id, raw[:200]), self.cfg)
            return True
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            log("发送失败 HTTP %s %s" % (e.code, detail), self.cfg)
            return False
        except Exception as e:
            log("发送失败: %r" % (e,), self.cfg)
            return False


def _kw_hit(kw, text_low, text):
    kwl = kw.lower()
    if re.fullmatch(r"[a-z0-9_]+", kwl):
        return re.search(r"(?<![a-z0-9])" + re.escape(kwl) + r"(?![a-z0-9])", text_low) is not None
    return kw in text


def format_usage(out, err=""):
    """json usage 的回复：text 字段是文本曲线（宽 32，微信里不折行）。

    末尾补一行摘要与数据来源（json usage 里是独立字段）。微信通道不发 PNG
    （通道不保证支持图片），要图就用文本曲线 —— 多行由 json.dumps 转义成
    \\n，ClawBot 侧按原样显示多行（与「电量 详情」的多行排版同一套做法）。
    """
    d = _load_json(out)
    if d is None:
        if (out or "").strip():
            return (out or "").strip()[:400]
        return "⚠️ 查询失败：powerfee 无输出（%s）" % ((err or "").strip()[:120] or "rc!=0")
    text = (d.get("text") or "").strip()
    if d.get("ok") is False:
        # ok=false：窗口里没有可用数据（接口没数据且本地采样也不够）
        return "⚠️ 没有取到用电数据：%s" % (d.get("error") or "（窗口内没有数据）")
    if not text:
        return "⚠️ 没有取到用电数据：%s" % (d.get("error") or "（无数据）")
    lines = [text]
    if d.get("summary_line"):
        lines.append(str(d["summary_line"]))
    if d.get("source_text"):
        lines.append("数据来源：%s" % d["source_text"])
    return "\n".join(lines)[:1500]


def match_command(cfg, text):
    """返回 'help' / 'usage' / 'brief' / 'detail' / None。允许 / 前缀，大小写不敏感。"""
    t = text.strip()
    if not t:
        return None
    tl = t.lower()
    if any(_kw_hit(k, tl, t) for k in cfg.help_keywords):
        return "help"
    # 曲线关键词先于「电量」判断：「用电量」这类消息应该看曲线，不是单行余额
    if any(_kw_hit(k, tl, t) for k in cfg.usage_keywords):
        return "usage"
    if any(_kw_hit(k, tl, t) for k in cfg.query_keywords):
        if any(_kw_hit(k, tl, t) for k in cfg.detail_keywords):
            return "detail"
        return "brief"
    return None


class Throttle(object):
    """同一 (用户, 指令类别) 在 N 秒内只放行一次。"""

    def __init__(self, seconds):
        self.seconds = float(seconds)
        self.last = {}

    def allow(self, key, now=None):
        now = time.time() if now is None else now
        last = self.last.get(key, 0.0)
        if now - last < self.seconds:
            return False
        self.last[key] = now
        return True


class Dedup(object):
    """同一行日志只处理一次。

    带时间戳的行（docker logs -t / logread）用长窗口，因为时间戳+内容完全相同
    只可能是同一行被重读；不带时间戳的行用短窗口，避免误伤用户重复发送。
    """

    def __init__(self, short_seconds):
        self.short = float(short_seconds)
        self.seen = {}

    def is_dup(self, line, now=None):
        now = time.time() if now is None else now
        window = 900.0 if TS_RE.match(line) else self.short
        if len(self.seen) > 1000:
            self.seen = {k: v for k, v in self.seen.items() if now - v[0] < v[1]}
        hit = self.seen.get(line)
        self.seen[line] = (now, window)
        return bool(hit) and (now - hit[0]) < min(window, hit[1])


# --------------------------------------------------------------- pipeline ---
def process_line(cfg, sender, raw, now=None, runner=None, throttle=None, dedup=None):
    """处理一行日志。返回动作字符串（用于测试断言）。"""
    now = time.time() if now is None else now
    line = ANSI_RE.sub("", raw).rstrip("\r\n")
    if not line.strip():
        return "empty"
    # 防自回环：syslog 里本桥接自己的行（例如 "处理: replied:brief | <原始消息行>"）
    # 也包含完整的 "[Bot: ... | Message from ...]" 文本；按 tag 直接丢弃。
    mtag = SYSLOG_TAG_RE.match(line)
    if mtag and mtag.group("tag") in cfg.ignore_tags:
        return "ignored-tag"
    m = MSG_RE.search(line)
    if not m:
        return "noise"                     # 二维码字符画 / 启动横幅 / 提示符等
    if dedup is not None and dedup.is_dup(line, now):
        return "dup"
    bot = m.group("bot").strip()
    frm = m.group("from").strip()
    text = m.group("text").strip()
    if not text:
        return "empty-text"
    if frm == bot or frm in cfg.ignore_from:
        return "ignored"                   # 防回环 / 黑名单
    cmd = match_command(cfg, text)
    if not cmd:
        return "no-match"
    if throttle is not None and not throttle.allow((frm, cmd), now):
        return "throttled"
    if cmd == "help":
        reply = HELP_TEXT
    elif cmd == "brief":
        rc, out, err = run_powerfee(cfg, ["json", "brief"], runner)
        reply = parse_brief(out, err)
    elif cmd == "usage":
        rc, out, err = run_powerfee(cfg, ["json", "usage", "--days", str(cfg.usage_days)], runner)
        reply = format_usage(out, err)
    else:
        rc, out, err = run_powerfee(cfg, ["json", "status"], runner)
        reply = format_detail(out, err)
    ok = sender.send(bot, reply)
    return ("replied:" if ok else "send-failed:") + cmd


# ------------------------------------------------------------------ source ---
def source_command(cfg):
    if cfg.source == "docker":
        return ([cfg.docker_bin, "logs", "-f", "-t"]
                + shlex.split(cfg.docker_log_args) + [cfg.container])
    if cfg.source == "logfile":
        return [cfg.tail_bin, "-F", "-n", "0", cfg.logfile]
    if cfg.source == "logread":
        cmd = [cfg.logread_bin, "-f"]
        if cfg.logread_filter:
            cmd += ["-e", cfg.logread_filter]
        return cmd
    raise ValueError("未知 SOURCE: %r（可选 docker/logfile/logread）" % cfg.source)


def _sigterm(signum, frame):
    p = _CHILD.get("p")
    if p is not None:
        try:
            p.terminate()
        except Exception:
            pass
    sys.exit(0)


def stream_forever(cfg):
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    throttle = Throttle(cfg.throttle_seconds)
    dedup = Dedup(cfg.dedup_seconds)
    sender = Sender(cfg)
    backoff = 1.0
    while True:
        cmd = source_command(cfg)
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1, errors="replace")
        except Exception as e:
            log("启动日志源失败: %r（%ss 后重试）" % (e, int(backoff)), cfg)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        _CHILD["p"] = p
        log("日志源已连接 pid=%s: %s" % (p.pid, " ".join(cmd)), cfg)
        started = time.time()
        last_line = ""
        try:
            for raw in p.stdout:
                last_line = raw.strip()
                try:
                    res = process_line(cfg, sender, raw, throttle=throttle, dedup=dedup)
                    if res.startswith("replied") or res.startswith("send-failed"):
                        log("处理: %s | %s" % (res, raw.strip()[:160]), cfg)
                except Exception as e:
                    log("处理行异常: %r" % (e,), cfg)
        except Exception as e:
            log("读取日志流异常: %r" % (e,), cfg)
        try:
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        alive = time.time() - started
        if alive >= 30.0:
            backoff = 1.0            # 稳定连接过才重置退避，避免秒断秒连刷屏
        msg = "日志源断开（rc=%s, 存活 %.0fs）" % (p.returncode, alive)
        if p.returncode not in (0, None) and last_line:
            msg += "，最后一行: %s" % last_line[:200]
        log(msg + "，%ss 后重连" % int(backoff), cfg)
        time.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


# --------------------------------------------------------------- 每日推送 ---
def parse_hhmm(s):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (s or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h < 24 and 0 <= mi < 60:
        return h, mi
    return None


def daily_push_loop(cfg, sender):
    tgt = parse_hhmm(cfg.daily_push_time)
    if not tgt or not cfg.bot_id:
        return
    log("每日定时推送已启用: %02d:%02d" % tgt, cfg)
    last_date = ""
    while True:
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if now.tm_hour == tgt[0] and now.tm_min == tgt[1] and last_date != today:
            last_date = today
            rc, out, err = run_powerfee(cfg, ["json", "brief"])
            sender.send(cfg.bot_id, parse_brief(out, err))
        time.sleep(20)


def run_daemon(cfg):
    if not cfg.token:
        log("FATAL 没有可用的 api_token（检查 AUTH_JSON=%s 或 API_TOKEN）" % cfg.auth_json, cfg)
        return 2
    try:
        cmd = source_command(cfg)
    except Exception as e:
        log("FATAL %s" % e, cfg)
        return 2
    log("powerfee-chat 启动 source=%s cmd=%s bot=%s" % (cfg.source, " ".join(cmd),
                                                        cfg.bot_id or "(未配置)"), cfg)
    threading.Thread(target=daily_push_loop, args=(cfg, Sender(cfg)), daemon=True).start()
    stream_forever(cfg)
    return 0


# ------------------------------------------------------------------- modes ---
def run_once_line(cfg, line):
    sender = Sender(cfg)
    res = process_line(cfg, sender, line, throttle=Throttle(cfg.throttle_seconds),
                       dedup=Dedup(cfg.dedup_seconds))
    print("RESULT: %s" % res)
    return 0


def run_send_text(cfg, text, bot_id=None):
    sender = Sender(cfg)
    ok = sender.send(bot_id or cfg.bot_id, text)
    print("RESULT: %s" % ("sent" if ok else "failed"))
    return 0 if ok else 1


def print_config(cfg):
    print("SOURCE          = %s" % cfg.source)
    print("source command  = %s" % " ".join(source_command(cfg)))
    print("API_BASE        = %s" % cfg.api_base)
    print("BOT_ID          = %s" % (cfg.bot_id or "(未配置)"))
    print("AUTH_JSON       = %s" % cfg.auth_json)
    print("api_token       = %s" % ("<已解析，长度 %d>" % len(cfg.token) if cfg.token
                                    else "<空！>"))
    print("POWERFEE_BIN    = %s" % cfg.powerfee_bin)
    print("QUERY_KEYWORDS  = %s" % ",".join(cfg.query_keywords))
    print("DETAIL_KEYWORDS = %s" % ",".join(cfg.detail_keywords))
    print("HELP_KEYWORDS   = %s" % ",".join(cfg.help_keywords))
    print("USAGE_KEYWORDS  = %s" % ",".join(cfg.usage_keywords))
    print("USAGE_DAYS      = %s" % cfg.usage_days)
    print("THROTTLE_SECONDS= %s" % cfg.throttle_seconds)
    print("DEDUP_SECONDS   = %s" % cfg.dedup_seconds)
    print("IGNORE_FROM     = %s" % ",".join(cfg.ignore_from))
    print("IGNORE_TAGS     = %s" % ",".join(cfg.ignore_tags))
    print("LOGREAD_FILTER  = %s" % (cfg.logread_filter or "(不过滤，全量扫描)"))
    print("DAILY_PUSH_TIME = %s" % (cfg.daily_push_time or "(关闭)"))
    print("LOG_FILE        = %s" % (cfg.log_file or "(仅 syslog)"))
    return 0


# ---------------------------------------------------------------- selftest ---
BRIEF_FIXTURE = json.dumps({
    "ok": True,
    "text": "✅ 宿舍电量当前状态：A栋 101 剩余 506.99 度（阈值 40 度）",
    "balance": 506.99, "level": "ok", "unit": "度",
}, ensure_ascii=False)

STATUS_FIXTURE = json.dumps({
    "version": "1.0.1", "configured": True, "room_num": "1001",
    "room_display": "A栋 101", "campus": "东校区", "building": "A栋", "room": "101",
    "balance": 506.99, "level": "ok", "unit": "度", "threshold": 40,
    "daily": None, "days_left": None, "last_ok_at": 1791373767, "last_error": "",
    "poll_count": 4, "interval": 1800, "cooldown": 180,
}, ensure_ascii=False)

# json usage 的回复：text 是文本曲线（宽 32），摘要与来源是独立字段
USAGE_FIXTURE = json.dumps({
    "ok": True, "source": "api", "unit": "度", "empty": False,
    "from": "2026-10-01", "to": "2026-10-07",
    "days": [{"date": "2026-10-0%d" % i, "used": 10.0 + i, "incomplete": False}
             for i in range(1, 8)],
    "summary": {"total": 98.0, "avg": 14.0, "max": 17.0, "max_date": "2026-10-07",
                "min": 11.0, "min_date": "2026-10-01", "count": 7, "missing": 0,
                "incomplete": 0},
    "text": ("近 7 天用电量（度）\n"
             "17 ┤      ██\n"
             "   │   ▅▅ ██\n"
             "   │▄▄██▄▄██\n"
             "11 ┼▄▄██▄▄██\n"
             "    10-01 10-07"),
    "summary_line": "合计 98.00 度 · 日均 14.00 度",
    "source_text": "学校接口逐日数据",
}, ensure_ascii=False)


class FakeAPI(object):
    """本地假发送 API：收包并断言。"""

    def __init__(self):
        import http.server
        outer = self
        self.requests = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8", "replace")
                outer.requests.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "ctype": self.headers.get("Content-Type"),
                    "body": body,
                })
                data = b'{"code":200,"message":"OK"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


class FakePowerfee(object):
    def __init__(self):
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        if cmd and cmd[-1] == "brief":
            return 0, BRIEF_FIXTURE, ""
        if "usage" in cmd:
            return 0, USAGE_FIXTURE, ""
        return 0, STATUS_FIXTURE, ""


def run_selftest():
    api = FakeAPI()
    pf = FakePowerfee()
    checks = []

    def check(name, cond, extra=""):
        checks.append((name, bool(cond)))
        print("[selftest] %s %s%s" % ("PASS" if cond else "FAIL", name,
                                      ("  <- %s" % extra) if extra else ""))

    def new_cfg():
        c = Config(None)
        c.api_base = "http://127.0.0.1:%d" % api.port
        c.bot_id = "a1b2c3d4e5f6@im.bot"
        c.api_token = "test-token-abcdefghijkl"
        c.token = c.api_token
        c.powerfee_bin = "/usr/bin/powerfee"
        c.ignore_from = []
        return c

    def feed(cfg, sender, line, t, thr, ded, pfobj=pf):
        return process_line(cfg, sender, line, now=t, runner=pfobj,
                            throttle=thr, dedup=ded)

    def msg_line(text, frm="o1234567890abcdefghijklmnop@im.wechat"):
        return ("[Bot: a1b2c3d4e5f6@im.bot | Message from %s]: %s" % (frm, text))

    t0 = time.time()
    try:
        # --- 1. 命中关键词 -> 调 brief -> POST 内容正确 ---
        cfg = new_cfg()
        sender = Sender(cfg)
        thr, ded = Throttle(3.0), Dedup(5.0)
        n0 = len(api.requests)
        r = feed(cfg, sender, msg_line("电量"), t0, thr, ded)
        check("1a 关键词命中回 brief", r == "replied:brief", r)
        check("1b 调用了 powerfee json brief",
              pf.calls and pf.calls[-1] == ["/usr/bin/powerfee", "json", "brief"],
              repr(pf.calls[-1:] if pf.calls else None))
        reqs = api.requests[n0:]
        check("1c 只发了一条消息", len(reqs) == 1, "n=%d" % len(reqs))
        if reqs:
            body = json.loads(reqs[0]["body"])
            check("1d 请求体 text 等于 brief 摘要", body == {"text": json.loads(BRIEF_FIXTURE)["text"]},
                  reqs[0]["body"][:120])
            check("1e Authorization 头正确",
                  reqs[0]["auth"] == "Bearer test-token-abcdefghijkl", str(reqs[0]["auth"]))
            check("1f 路径正确", reqs[0]["path"] == "/bots/a1b2c3d4e5f6@im.bot/messages",
                  reqs[0]["path"])
        else:
            check("1d 请求体 text 等于 brief 摘要", False)
            check("1e Authorization 头正确", False)
            check("1f 路径正确", False)

        # --- 2. 详情关键词 -> status -> 多行排版 ---
        n1 = len(api.requests)
        r = feed(cfg, sender, msg_line("电量 详情"), t0 + 0.2, thr, ded)
        check("2a 详情命中回 detail", r == "replied:detail", r)
        check("2b 调用了 powerfee json status",
              pf.calls[-1] == ["/usr/bin/powerfee", "json", "status"],
              repr(pf.calls[-1]))
        reqs = api.requests[n1:]
        if reqs:
            text = json.loads(reqs[0]["body"])["text"]
            lines = text.split("\n")
            check("2c 详情排版 <=6 行", len(lines) <= 6, "lines=%d" % len(lines))
            check("2d 详情含房间与余额", "A栋 101" in text and "506.99" in text,
                  text.replace("\n", " / ")[:140])
        else:
            check("2c 详情排版 <=6 行", False)
            check("2d 详情含房间与余额", False)

        # --- 3. /前缀 + 大小写不敏感 ---
        n2 = len(api.requests)
        r = feed(cfg, sender, msg_line("/电量"), t0 + 0.4, thr, ded)
        check("3a /前缀不回复(3秒节流)", r == "throttled", r)
        check("3b 节流期间没有新请求", len(api.requests) == n2, "n=%d" % len(api.requests))
        r = feed(cfg, sender, msg_line("/BALANCE"), t0 + 4.0, thr, ded)
        check("3c 大小写不敏感+斜杠前缀可命中", r == "replied:brief", r)
        r = feed(cfg, sender, msg_line("电费"), t0 + 4.2, thr, ded)
        check("3d 同用户3秒内再次查询被节流", r == "throttled", r)

        # --- 4. 不命中关键词不回复 ---
        n3 = len(api.requests)
        r = feed(cfg, sender, msg_line("今天天气不错"), t0 + 8.0, thr, ded)
        check("4a 普通聊天不回复", r == "no-match", r)
        check("4b 没有新 HTTP 请求", len(api.requests) == n3, "n=%d" % len(api.requests))

        # --- 5. 帮助 ---
        n4 = len(api.requests)
        r = feed(cfg, sender, msg_line("帮助"), t0 + 9.0, thr, ded)
        check("5a help 回复", r == "replied:help", r)
        if len(api.requests) > n4:
            text = json.loads(api.requests[-1]["body"])["text"]
            check("5b help 内容含指令列表", "电量" in text and "帮助" in text,
                  text.replace("\n", " / ")[:120])
        else:
            check("5b help 内容含指令列表", False)

        # --- 6. 噪声行全部跳过 ---
        n5 = len(api.requests)
        noise = [
            "█████████████████████████████████████████",
            "▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
            "Please scan the QR code to log in",
            "[Bot: a1b2c3d4e5f6@im.bot] Started listening for messages...",
            "> ",
            "2026-10-07T12:11:28.734044617Z > ",
            "\x1b[32m[Bot: a1b2c3d4e5f6@im.bot] API Server listening\x1b[0m",
            "Wed Oct  7 20:55:48 2026 daemon.info weclawbot-api[8230]: ████ █   █ █▄▀█▄▀██▀ ██▀▀██▀█▀▀▄██ █   █ ████",
            "Wed Oct  7 20:56:06 2026 user.notice weclawbot-api: 服务停止",
            "Wed Oct  7 20:56:07 2026 user.notice weclawbot-api: 启动：端口 26322，配置目录 /etc/weclawbot",
        ]
        results = [feed(cfg, sender, ln, t0 + 10.0 + i, thr, ded) for i, ln in enumerate(noise)]
        check("6a 噪声/二维码行全部跳过", all(x == "noise" for x in results), repr(results))
        check("6b 噪声没有产生请求", len(api.requests) == n5, "n=%d" % len(api.requests))

        # --- 7. 重复行去重（docker --since 重读边界）---
        cfg7 = new_cfg()
        s7 = Sender(cfg7)
        thr7, ded7 = Throttle(3.0), Dedup(5.0)
        n6 = len(api.requests)
        dup_line = "2026-10-07T12:11:23.513869226Z " + msg_line("电量")
        r1 = feed(cfg7, s7, dup_line, t0 + 20.0, thr7, ded7)
        r2 = feed(cfg7, s7, dup_line, t0 + 21.0, thr7, ded7)
        check("7a 首次处理", r1 == "replied:brief", r1)
        check("7b 同一条日志行重复出现被去重", r2 == "dup", r2)
        check("7c 去重后没有多发消息", len(api.requests) == n6 + 1,
              "n=%d" % (len(api.requests) - n6))

        # --- 8. 防回环：from 等于 bot 自己 / 在 IGNORE_FROM 里 ---
        cfg8 = new_cfg()
        cfg8.ignore_from = ["someone-else@im.wechat"]
        s8 = Sender(cfg8)
        thr8, ded8 = Throttle(3.0), Dedup(5.0)
        n7 = len(api.requests)
        r = feed(cfg8, s8, msg_line("电量", frm="a1b2c3d4e5f6@im.bot"), t0 + 30.0, thr8, ded8)
        check("8a from==bot 自己 -> ignored", r == "ignored", r)
        r = feed(cfg8, s8, msg_line("电量", frm="someone-else@im.wechat"), t0 + 30.1, thr8, ded8)
        check("8b IGNORE_FROM 命中 -> ignored", r == "ignored", r)
        check("8c 被忽略的行没有发消息", len(api.requests) == n7, "n=%d" % len(api.requests))

        # --- 8d~8f. syslog 形态解析 + 自家 tag 防自回环 ---
        cfg8b = new_cfg()
        s8b = Sender(cfg8b)
        thr8b, ded8b = Throttle(3.0), Dedup(5.0)
        n8 = len(api.requests)
        syslog_msg = ("Wed Oct  7 21:05:00 2026 user.notice weclawbot-api: " + msg_line("电量"))
        r = feed(cfg8b, s8b, syslog_msg, t0 + 31.0, thr8b, ded8b)
        check("8d syslog 前缀的原生服务消息行可解析", r == "replied:brief", r)
        self_line = ("Wed Oct  7 21:05:01 2026 user.notice powerfee-chat: 处理: replied:brief | "
                     + msg_line("电量"))
        r = feed(cfg8b, s8b, self_line, t0 + 31.1, thr8b, ded8b)
        check("8e 自家 tag(powerfee-chat) 的行被忽略（防自回环）", r == "ignored-tag", r)
        check("8f 自家行没有发消息", len(api.requests) == n8 + 1, "n=%d" % (len(api.requests) - n8))
        sh_line = ("Wed Oct  7 20:58:05 2026 daemon.info sh[9104]: " + msg_line("电量 详情"))
        r = feed(cfg8b, s8b, sh_line, t0 + 31.2, thr8b, ded8b)
        check("8g 原生服务实际 tag(sh[pid]) 的消息行可解析", r == "replied:detail", r)
        check("8h 计数正确", len(api.requests) == n8 + 2, "n=%d" % (len(api.requests) - n8))

        # --- 9. 每日推送时间解析 ---
        check("9a HH:MM 解析", parse_hhmm("07:30") == (7, 30), repr(parse_hhmm("07:30")))
        check("9b 空值=关闭", parse_hhmm("") is None, repr(parse_hhmm("")))
        check("9c 非法值拒绝", parse_hhmm("25:00") is None, repr(parse_hhmm("25:00")))

        # --- 10. 三种日志来源的命令行 ---
        c10 = new_cfg()
        c10.source = "docker"
        check("10a docker 源命令",
              source_command(c10) == ["/usr/bin/docker", "logs", "-f", "-t", "--since", "1s",
                                      "weclawbot-api"], repr(source_command(c10)))
        c10.source = "logfile"
        c10.logfile = "/var/log/weclawbot.log"
        check("10b logfile 源命令",
              source_command(c10) == ["/usr/bin/tail", "-F", "-n", "0", "/var/log/weclawbot.log"],
              repr(source_command(c10)))
        c10.source = "logread"
        c10.logread_filter = "weclawbot"
        check("10c logread 源命令",
              source_command(c10) == ["/sbin/logread", "-f", "-e", "weclawbot"],
              repr(source_command(c10)))
        c10.logread_filter = ""
        check("10d logread 无过滤",
              source_command(c10) == ["/sbin/logread", "-f"], repr(source_command(c10)))

        # --- 11. 默认配置指向原生服务（logread）---
        d11 = Config(None)
        check("11a 默认 SOURCE=logread（原生优先）", d11.source == "logread", d11.source)
        check("11b 默认 LOGREAD_FILTER=Message from", d11.logread_filter == "Message from",
              d11.logread_filter)
        check("11c 默认忽略自家 tag", "powerfee-chat" in d11.ignore_tags, repr(d11.ignore_tags))

        # --- 12. 每日用电量曲线（关键词 -> json usage --days N -> 文本曲线）---
        cfg12 = new_cfg()
        s12 = Sender(cfg12)
        thr12, ded12 = Throttle(3.0), Dedup(5.0)
        n9 = len(api.requests)
        r = feed(cfg12, s12, msg_line("曲线"), t0 + 40.0, thr12, ded12)
        check("12a 「曲线」命中 usage 并回复", r == "replied:usage", r)
        check("12b 调用了 powerfee json usage --days 7",
              pf.calls[-1] == ["/usr/bin/powerfee", "json", "usage", "--days", "7"],
              repr(pf.calls[-1]))
        reqs = api.requests[n9:]
        if reqs:
            text = json.loads(reqs[0]["body"])["text"]
            check("12c 回复是文本曲线 + 摘要（含柱形字符与合计）",
                  "\u2588" in text and "\u5408\u8ba1" in text and "\n" in text,
                  text.replace("\n", " / ")[:140])
            check("12d 多行排版不超过 12 行（微信里不刷屏）",
                  len(text.split("\n")) <= 12, "lines=%d" % len(text.split("\n")))
        else:
            check("12c 回复是文本曲线 + 摘要（含柱形字符与合计）", False)
            check("12d 多行排版不超过 12 行（微信里不刷屏）", False)
        r = feed(cfg12, s12, msg_line("用电量"), t0 + 45.0, thr12, ded12)
        check("12e 「用电量」命中曲线（曲线关键词先于「电量」）", r == "replied:usage", r)
        r = feed(cfg12, s12, msg_line("电量"), t0 + 46.0, thr12, ded12)
        check("12f 纯「电量」仍是 brief（老行为回归）", r == "replied:brief", r)
        d12 = Config(None)
        check("12g 默认曲线关键词含「曲线/用电/用量」",
              all(k in d12.usage_keywords for k in ("\u66f2\u7ebf", "\u7528\u7535", "\u7528\u91cf")),
              repr(d12.usage_keywords))
        check("12h 默认曲线天数 = 7", d12.usage_days == 7, repr(d12.usage_days))
    finally:
        api.close()

    failed = [n for n, ok in checks if not ok]
    print("[selftest] RESULT: %s (%d/%d checks passed)" % (
        "ALL PASS" if not failed else "FAILURES: " + "; ".join(failed),
        len(checks) - len(failed), len(checks)))
    return 0 if not failed else 1


# -------------------------------------------------------------------- main ---
def main(argv=None):
    ap = argparse.ArgumentParser(description="powerfee-chat: WeChat ClawBot bridge")
    ap.add_argument("--config", default=DEFAULT_CONF)
    ap.add_argument("--daemon", action="store_true", help="常驻运行（默认）")
    ap.add_argument("--once-line", metavar="LINE", help="把一条日志行喂进真实管线")
    ap.add_argument("--send-text", metavar="TEXT", help="只发一条消息（诊断）")
    ap.add_argument("--to", metavar="BOT_ID", help="配合 --send-text 指定 bot_id")
    ap.add_argument("--selftest", action="store_true", help="离线自测")
    ap.add_argument("--check-config", action="store_true", help="打印生效配置")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()
    cfg = Config(args.config)
    if args.check_config:
        return print_config(cfg)
    if args.once_line is not None:
        return run_once_line(cfg, args.once_line)
    if args.send_text is not None:
        return run_send_text(cfg, args.send_text, args.to)
    return run_daemon(cfg)


if __name__ == "__main__":
    sys.exit(main())
