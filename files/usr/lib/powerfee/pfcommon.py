#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""powerfee 推送助手共用件。

`wechat.py`（ClawBot 五步向导）与 `push.py`（推送通道管理：Server酱 / 企业微信 /
ClawBot / 自定义 webhook / 关闭）都从这里 import —— 子进程、UCI 读写、JSON 输出、
ClawBot 服务探测、推送状态读取这些公共部分只在这里写一份。

为什么单独一个文件：两个助手都要「探测微信服务形态 / 读 auth.json / 写 notify 段 /
读最近一次推送结果」，复制粘贴两份必然会走偏（改了一处忘了另一处）。
两个助手都是 `python3 /usr/lib/powerfee/<x>.py` 这样被调用的，同目录 import 天然可用。
"""

import io
import json
import os
import re
import socket
import subprocess
import sys
import time

# ---- 路径与常量 -------------------------------------------------------------

NATIVE_INIT = "/etc/init.d/weclawbot-api"
NATIVE_BIN = "/usr/bin/weclawbot-api"
NATIVE_UCI = "/etc/config/weclawbot-api"
NATIVE_AUTH = "/etc/weclawbot/config/auth.json"
# 原生包可能把 stdout/stderr 落到这些文件之一（procd 也常直接进 syslog）
NATIVE_LOGS = [
    "/var/log/weclawbot-api.log",
    "/tmp/log/weclawbot-api.log",
    "/var/log/weclawbot.log",
    "/etc/weclawbot/weclawbot-api.log",
    "/etc/weclawbot/log/weclawbot-api.log",
]

DOCKER_NAME = "weclawbot-api"
DOCKER_AUTH = "/opt/weclawbot/config/auth.json"

DEFAULT_PORT = 26322

PF_BIN = "/usr/bin/powerfee"
PF_LOG_DEFAULT = "/etc/powerfee/powerfee.log"

# stderr 上的人话前缀：wechat.py 会把它改成 "wechat"（保持 1.0.2 的报错文本不变）
TAG = "powerfee"

# powerfee 日志里「推送结果」那几行（notify_send 写的）
PUSH_RE = re.compile(r"通知已推送|通知未发送|通知推送失败")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老 python 没有 reconfigure
    pass


# ---- 小工具 -----------------------------------------------------------------

def sh(args, timeout=20, input_text=None):
    """跑一条命令，返回 (rc, stdout, stderr)；找不到命令时 rc=127。"""
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           input=input_text, timeout=timeout)
        return (p.returncode,
                p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))
    except FileNotFoundError:
        return 127, "", "%s: not found" % args[0]
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timeout after %ss" % (args[0], timeout)


def out_json(obj, rc=0):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return rc


def fail(msg, rc=1, **extra):
    """业务失败：JSON 走 stdout（给页面/CGI），人话走 stderr。"""
    obj = {"ok": False, "error": msg}
    obj.update(extra)
    sys.stderr.write("%s: %s\n" % (TAG, msg))
    return out_json(obj, rc)


def uci_get(key, default=""):
    rc, o, _ = sh(["uci", "-q", "get", key], timeout=10)
    v = o.strip()
    return v if rc == 0 and v else default


def uci_show_section(section):
    """uci show powerfee.notify -> {option: value}"""
    rc, o, _ = sh(["uci", "-q", "show", section], timeout=10)
    vals = {}
    if rc != 0:
        return vals
    for line in o.splitlines():
        m = re.match(r"^[^.]+\.[^.]+\.([A-Za-z0-9_]+)='(.*)'$", line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
        else:
            m = re.match(r"^[^.]+\.[^.]+\.([A-Za-z0-9_]+)=(.*)$", line.strip())
            if m:
                vals[m.group(1)] = m.group(2)
    return vals


def uci_apply(pairs, deletes=(), commit=True):
    """批量写 UCI：pairs=[("powerfee.notify.url", "https://…")]，deletes=[键]。

    值直接作为 argv 传给 uci（不过 shell），所以 JSON 里的引号、URL 里的 = 都不会被
    二次解释。返回 (ok, err)；err 是人话，可直接回给页面。
    """
    for key, val in pairs:
        rc, o, e = sh(["uci", "set", "%s=%s" % (key, val)], timeout=10)
        if rc != 0:
            return False, "写配置失败（uci set %s）：%s" % (key, (e or o).strip())
    for key in deletes:
        # 键不存在时 delete 会报错，-q 静默；删不掉也不该让整条流水线失败
        sh(["uci", "-q", "delete", key], timeout=10)
    if commit:
        rc, o, e = sh(["uci", "commit", "powerfee"], timeout=15)
        if rc != 0:
            return False, "uci commit powerfee 失败：%s" % (e or o).strip()
    return True, ""


# ---- 服务形态探测（原生优先，Docker 回退）-----------------------------------

class Service(object):
    def __init__(self):
        self.mode = "none"
        self.running = False
        self.auth_path = ""
        self.log_source = ""
        self.port = DEFAULT_PORT

    def detect(self):
        native = (os.path.exists(NATIVE_INIT) or os.path.exists(NATIVE_BIN)
                  or os.path.exists(NATIVE_AUTH))
        if native:
            self.mode = "native"
            self.auth_path = NATIVE_AUTH if os.path.exists(NATIVE_AUTH) else ""
            self.running = self._native_running()
            self.port = self._native_port()
            return self
        if self._docker_exists():
            self.mode = "docker"
            self.auth_path = DOCKER_AUTH if os.path.exists(DOCKER_AUTH) else ""
            self.running = self._docker_running()
            self.port = self._docker_port()
            return self
        # 两种都没有：仍可能只是配置目录在（比如容器被删了），保持 mode=none
        self.mode = "none"
        if os.path.exists(DOCKER_AUTH) or os.path.exists(NATIVE_AUTH):
            self.auth_path = DOCKER_AUTH if os.path.exists(DOCKER_AUTH) else NATIVE_AUTH
        return self

    # --- native ---
    def _native_running(self):
        if os.path.exists(NATIVE_INIT):
            rc, o, _ = sh([NATIVE_INIT, "status"], timeout=15)
            if rc == 0 and o.strip() == "running":
                return True
        rc, o, _ = sh(["pgrep", "-f", "weclawbot-api"], timeout=10)
        return rc == 0 and bool(o.strip())

    def _native_port(self):
        try:
            with io.open(NATIVE_UCI, encoding="utf-8", errors="replace") as fh:
                m = re.search(r"^\s*option\s+port\s+'?(\d+)'?\s*$", fh.read(), re.M)
            if m:
                return int(m.group(1))
        except IOError:
            pass
        p = uci_get("weclawbot-api.main.port") or uci_get("weclawbot-api.@server[0].port")
        if p.isdigit():
            return int(p)
        return DEFAULT_PORT

    # --- docker ---
    def _docker_exists(self):
        rc, o, _ = sh(["docker", "inspect", "-f", "{{.Name}}", DOCKER_NAME], timeout=20)
        return rc == 0 and o.strip() != ""

    def _docker_running(self):
        rc, o, _ = sh(["docker", "inspect", "-f", "{{.State.Running}}", DOCKER_NAME], timeout=20)
        return rc == 0 and o.strip() == "true"

    def _docker_port(self):
        rc, o, _ = sh(["docker", "port", DOCKER_NAME, "26322/tcp"], timeout=20)
        if rc == 0:
            m = re.search(r":(\d+)\s*$", o.strip().splitlines()[0] if o.strip() else "")
            if m:
                return int(m.group(1))
        return DEFAULT_PORT

    # --- 日志 ---
    def log_text(self, tail_lines=400):
        """返回 (text, source, mtime)；先原生后 Docker。"""
        if self.mode in ("native", "none"):
            for path in NATIVE_LOGS:
                if os.path.exists(path):
                    try:
                        with io.open(path, "rb") as fh:
                            fh.seek(0, os.SEEK_END)
                            size = fh.tell()
                            fh.seek(max(0, size - 256 * 1024))
                            data = fh.read().decode("utf-8", "replace")
                        return data, path, os.path.getmtime(path)
                    except IOError:
                        pass
            rc, o, _ = sh(["logread", "-e", "weclawbot"], timeout=20)
            if rc == 0 and o.strip():
                return o, "logread -e weclawbot", None
        if self.mode == "docker" or self._docker_exists():
            rc, o, e = sh(["docker", "logs", "--timestamps", "--tail", str(tail_lines),
                           DOCKER_NAME], timeout=30)
            if rc == 0 and o.strip():
                return o, "docker logs %s" % DOCKER_NAME, None
            if rc != 0:
                sys.stderr.write("%s: docker logs 失败：%s\n" % (TAG, (e or o).strip()[:200]))
        return "", "", None


# ---- ClawBot 凭据 -----------------------------------------------------------

def read_auth(svc):
    """读 auth.json，返回 {bot_id: bot_dict}（绝不外传 token 值）。"""
    if not svc.auth_path or not os.path.exists(svc.auth_path):
        return {}
    try:
        with io.open(svc.auth_path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("%s: 读取 %s 失败：%s\n" % (TAG, svc.auth_path, exc))
        return {}
    bots = data.get("bots") or {}
    return bots if isinstance(bots, dict) else {}


def pick_bot(bots, want=None):
    """选一个 bot：(bot_id, bot) 或 (None, None)。优先已激活(context_token)的。"""
    if want and want in bots:
        return want, bots[want]
    ready = [(k, v) for k, v in bots.items() if (v.get("context_token") or "").strip()]
    if ready:
        return ready[-1]
    with_token = [(k, v) for k, v in bots.items() if (v.get("api_token") or "").strip()]
    if with_token:
        return with_token[-1]
    if bots:
        k = list(bots)[-1]
        return k, bots[k]
    return None, None


def clawbot_pick(want=None):
    """探测服务并挑一个能用的 bot。

    返回 (svc, bot_id, bot, err)：err 非空表示不能启用，文案直接给用户看。
    """
    svc = Service().detect()
    bots = read_auth(svc)
    bot_id, bot = pick_bot(bots, want)
    if not bot_id:
        return svc, None, None, ("没有找到已登录的 Bot：%s 里还没有 bots"
                                 "（先在「微信推送」页扫码登录）"
                                 % (svc.auth_path or "auth.json"))
    if not (bot.get("api_token") or "").strip():
        return svc, bot_id, bot, "Bot %s 还没有 api_token（服务可能仍在登录中，稍后重试）" % bot_id
    if not (bot.get("context_token") or "").strip():
        return svc, bot_id, bot, ("Bot %s 还没激活：先在微信里给它发一条消息"
                                  "（激活 context_token）再回来点一键启用" % bot_id)
    if not svc.running:
        return svc, bot_id, bot, "微信服务没有在运行（%s 形态），先启动服务再一键启用" % svc.mode
    return svc, bot_id, bot, None


def clawbot_uci(bot_id, api_token, port):
    """ClawBot 通道要写的 UCI 键值对 + 推送地址（token 只出现在 pairs 里）。"""
    url = "http://127.0.0.1:%d/bots/%s/messages" % (port, bot_id)
    pairs = [
        ("powerfee.notify.enabled", "1"),
        ("powerfee.notify.url", url),
        ("powerfee.notify.method", "POST"),
        ("powerfee.notify.content_type", "application/json"),
        ("powerfee.notify.body", '{"text":"{text}"}'),
        ("powerfee.notify.token_header", "Authorization"),
        # 服务端要的是 "Authorization: Bearer <api_token>"，
        # 而 powerfee 把 token 原样放进请求头，所以这里带 "Bearer " 前缀。
        ("powerfee.notify.token", "Bearer " + api_token),
        ("powerfee.notify.timeout", "15"),
    ]
    return url, pairs


# ---- 推送状态 ---------------------------------------------------------------

def notify_state():
    """powerfee.notify 段的当前值（enabled 转成 bool）。"""
    vals = uci_show_section("powerfee.notify")
    return {
        "section": bool(vals),
        "enabled": vals.get("enabled", "0") == "1",
        "url": vals.get("url", ""),
        "body": vals.get("body", ""),
        "method": (vals.get("method", "") or "POST").upper(),
        "content_type": vals.get("content_type", "") or "application/json",
        "timeout": vals.get("timeout", ""),
        "token_set": bool(vals.get("token", "").strip()),
        "token_header": vals.get("token_header", ""),
    }


def last_push_line():
    """最近一条推送结果（powerfee 日志里含「通知已推送/通知未发送/通知推送失败」的行）。"""
    log_file = uci_get("powerfee.main.log_file", PF_LOG_DEFAULT) or PF_LOG_DEFAULT
    text = ""
    if os.path.exists(log_file):
        try:
            with io.open(log_file, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 64 * 1024))
                text = fh.read().decode("utf-8", "replace")
        except IOError:
            text = ""
    if not text:
        rc, o, _ = sh(["logread", "-e", "powerfee"], timeout=20)
        if rc == 0:
            text = o
    hit = ""
    for line in text.splitlines():
        if PUSH_RE.search(line):
            hit = line.strip()
    return hit[:300]


def last_push_result():
    """把最近一条推送日志解析成 {ok, http, text, url}（解析不出时只有 text）。"""
    line = last_push_line()
    res = {"ok": None, "http": "", "text": line, "url": ""}
    if not line:
        return res
    if "通知已推送" in line:
        res["ok"] = True
    elif "通知未发送" in line or "通知推送失败" in line:
        res["ok"] = False
    m = re.search(r"HTTP (\d{3})", line)
    if m:
        res["http"] = m.group(1)
    m = re.search(r"（(?:GET|POST) (\S+)）", line)
    if m:
        res["url"] = m.group(1)
    if res["ok"]:
        # HTTP 2xx 不代表服务端收了：Server酱 / 企业微信 是 200 + 正文里报错，
        # 正文（powerfee 1.0.3 起会记进这行日志）里有非 0 的 code/errcode 就算失败
        m = re.search(r'"(?:code|errcode)"\s*:\s*(-?\d+)', line)
        if m and m.group(1) != "0":
            res["ok"] = False
    return res


def notify_test():
    """真发一条测试消息（走 powerfee notify-test，与告警推送同一条流水线）。

    返回 (rc, message)：message 里可能带服务端原文（powerfee 1.0.3 起会打印）。
    """
    rc, o, e = sh([PF_BIN, "notify-test"], timeout=60)
    return rc, (o + e).strip()


def tcp_reachable(port, host="127.0.0.1", timeout=0.6):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        s.close()
