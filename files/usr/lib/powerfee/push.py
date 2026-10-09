#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""powerfee 的推送通道管理器：Server酱 / 企业微信群机器人 / ClawBot / 自定义 webhook / 关闭。

LuCI「服务 → 宿舍电量哨兵 → 微信推送」页顶部的通道选择器走的就是这里
（`powerfee push <子命令>`），也直接命令行可用：

    python3 /usr/lib/powerfee/push.py status
    python3 /usr/lib/powerfee/push.py set-serverchan <SendKey>
    python3 /usr/lib/powerfee/push.py set-wecom <webhook-url>
    python3 /usr/lib/powerfee/push.py set-webhook <url> [method] [content_type] [body]
    python3 /usr/lib/powerfee/push.py set-clawbot
    python3 /usr/lib/powerfee/push.py off
    python3 /usr/lib/powerfee/push.py test

设计要点
--------
* **一条流水线**：写 powerfee.notify 配置 → `uci commit powerfee` → 立刻发一条测试
  消息 → 把结果（含服务端原文）回报给调用方。五个通道走的是同一段代码，
  区别只在「写什么键值」。
* **服务端原文一定带回来**：Server酱 / 企业微信这类服务端出错时是 HTTP 200 +
  JSON 里报错（Server酱 `{"code":40001,"message":"[AUTH]错误的Key"}`；
  企业微信 `{"errcode":93000,"errmsg":"invalid webhook url"}`），只看 HTTP 状态码
  会把失败当成功。所以测试结果要连正文一起判断，失败时把原文交给页面显示。
* **凭据不出门**：status 只回掩码（url_masked / key_hint），绝不回完整 key 或 token；
  写进 UCI 的 token 只用于拼请求头。
* **只动 notify 段**：不碰 mail 段、api 段、main 段；`push off` 也只把 enabled 置 0，
  保留 url/body 方便下次一键恢复。

退出码：0 成功；1 业务失败（原因在 stderr / JSON 的 error 字段）。
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import parse_qsl, urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pfcommon  # noqa: E402
from pfcommon import (  # noqa: E402
    clawbot_pick, clawbot_uci, fail, last_push_line, last_push_result, notify_state,
    notify_test, out_json, uci_apply,
)

SERVERCHAN_HOST = "sctapi.ftqq.com"          # Server酱 经典版
SERVERCHAN_SUFFIX = ".push.ft07.com"         # Server酱³（key 以 sctp 开头）
WECOM_HOST = "qyapi.weixin.qq.com"           # 企业微信群机器人
CLAWBOT_RE = re.compile(r"^https?://127\.0\.0\.1:\d+/bots/([^/]+)/messages$")

SERVERCHAN_BODY = '{"title":"{title}","desp":"{text}"}'
WECOM_BODY = '{"msgtype":"text","text":{"content":"{text}"}}'
WEBHOOK_BODY = '{"text":"{text}"}'

CHANNEL_NAMES = {
    "serverchan": "Server酱",
    "wecom": "企业微信群机器人",
    "clawbot": "ClawBot 微信推送",
    "webhook": "自定义 webhook",
    "none": "未配置",
}

# 形如 <你的SendKey> / <key> 的占位符：不是密钥，只是提示
PLACEHOLDER_RE = re.compile(r"^<.*>$|你的|your[_-]?key|sendkey", re.I)


# ---- 通道识别与掩码 ---------------------------------------------------------

def detect_channel(url):
    """按 URL 判断当前配置属于哪个通道（不看 enabled —— 关了也要知道原来配的是谁）。"""
    u = (url or "").strip()
    if not u:
        return "none"
    if CLAWBOT_RE.match(u):
        return "clawbot"
    host = (urlsplit(u).hostname or "").lower()
    if host == SERVERCHAN_HOST or host.endswith(SERVERCHAN_SUFFIX):
        return "serverchan"
    if host == WECOM_HOST:
        return "wecom"
    return "webhook"


def is_placeholder_key(key):
    return bool(PLACEHOLDER_RE.search((key or "").strip()))


def mask_secret(text, keep=4):
    """把一段密钥变成可辨认但不可用的提示（前 4 位 + 长度）。"""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= keep:
        return "…（已保存，%d 位）" % len(t)
    return "%s…（已保存，%d 位）" % (t[:keep], len(t))


def serverchan_key_of(url):
    """从 Server酱 的 URL 里取出 key（只用于掩码显示）。"""
    path = urlsplit((url or "").strip()).path
    m = re.search(r"/(?:send/)?([^/]+)\.send$", path)
    return m.group(1) if m else ""


def wecom_key_of(url):
    """从企业微信 webhook 地址里取出 ?key= 的值（只用于掩码显示）。"""
    for k, v in parse_qsl(urlsplit((url or "").strip()).query, keep_blank_values=True):
        if k == "key":
            return v
    return ""


def serverchan_url(key):
    """SendKey -> 推送地址。返回 (url, err)。

    sctp… 形态（Server酱³）走 https://<key 里 sctp 后的数字>.push.ft07.com/send/<key>.send；
    其它走经典版 https://sctapi.ftqq.com/<key>.send。
    """
    k = (key or "").strip()
    if not k:
        return None, "请填写 SendKey（在 sct.ftqq.com 微信扫码登录后获取）"
    if is_placeholder_key(k):
        return None, "这看起来是文档里的占位符，不是真的 SendKey：%s" % k
    if re.search(r"\s", k):
        return None, "SendKey 里不能有空格（粘贴时可能多带了空白）"
    if k.startswith("sctp"):
        m = re.match(r"^sctp(\d+)", k)
        if not m:
            return None, ("这个 SendKey 以 sctp 开头，但后面没有数字，"
                          "解析不出推送域名（Server酱³ 的 key 形如 sctp1234t…）")
        return "https://%s.push.ft07.com/send/%s.send" % (m.group(1), k), ""
    return "https://sctapi.ftqq.com/%s.send" % k, ""


def mask_url(url, channel=None):
    """URL 掩码：Server酱 掩掉 key；其它通道掩掉查询串里像密钥的参数。"""
    u = (url or "").strip()
    if not u:
        return ""
    ch = channel or detect_channel(u)
    if ch == "serverchan":
        key = serverchan_key_of(u)
        if not key or is_placeholder_key(key):
            return u                      # 占位符不是密钥，原样显示（方便看出"还没填"）
        return u.replace(key, mask_secret(key))
    if ch == "clawbot":
        return u                          # 本机回环地址，bot_id 不算密钥
    parts = urlsplit(u)
    if parts.query:
        # 逐段处理，只改「名字像密钥」的参数值，其余原样保留
        # （不能整串 urlencode：掩码里的 …（） 会被百分号编码成乱码）
        segs = []
        for seg in parts.query.split("&"):
            if "=" in seg:
                k, v = seg.split("=", 1)
                if v and re.search(r"(?i)key|token|secret|sign|password|passwd|auth", k):
                    seg = "%s=%s" % (k, mask_secret(v))
            segs.append(seg)
        parts = parts._replace(query="&".join(segs))
        return urlunsplit(parts)
    return u


def channel_detail(channel, ns, url):
    """给页面用的「这个通道现在什么状态」：key_set / key_hint / bot_id / note。"""
    out = {"key_set": False, "key_hint": "", "bot_id": "", "note": ""}
    if channel == "serverchan":
        key = serverchan_key_of(url)
        if not key:
            out["key_hint"] = "还没填 SendKey"
        elif is_placeholder_key(key):
            out["key_hint"] = "还没填 SendKey（当前是文档里的占位符）"
        else:
            out["key_set"] = True
            out["key_hint"] = mask_secret(key)
    elif channel == "wecom":
        key = wecom_key_of(url)
        if not key:
            out["key_hint"] = "地址里没有 key 参数（企业微信 webhook 地址要带 ?key=…）"
        elif is_placeholder_key(key):
            out["key_hint"] = "还没填 webhook 地址（当前是占位符）"
        else:
            out["key_set"] = True
            out["key_hint"] = mask_secret(key)
    elif channel == "clawbot":
        m = CLAWBOT_RE.match(url or "")
        out["bot_id"] = m.group(1) if m else ""
        if out["bot_id"] and ns["token_set"]:
            out["key_set"] = True
            out["key_hint"] = "Bot %s 的 api_token 已配置（已隐藏）" % out["bot_id"]
        else:
            out["key_hint"] = "还没绑定 Bot / 没有 api_token"
    elif channel == "webhook":
        out["key_set"] = True            # 自定义通道：地址本身就是全部配置
        if ns["token_set"]:
            out["key_hint"] = "额外请求头 %s 已配置（已隐藏）" % (ns["token_header"] or "?")
        else:
            out["key_hint"] = "无鉴权（只用地址）"
    else:
        out["key_hint"] = "还没配置推送通道"
    return out


def status_obj():
    ns = notify_state()
    channel = detect_channel(ns["url"])
    det = channel_detail(channel, ns, ns["url"])
    last = last_push_result()
    return {
        "ok": True,
        "channel": channel,
        "channel_name": CHANNEL_NAMES.get(channel, channel),
        "enabled": ns["enabled"],
        "url_masked": mask_url(ns["url"], channel),
        "body": ns["body"],
        "method": ns["method"],
        "content_type": ns["content_type"],
        "timeout": ns["timeout"],
        "token_set": ns["token_set"],
        "token_header": ns["token_header"],
        "key_set": det["key_set"],
        "key_hint": det["key_hint"],
        "bot_id": det["bot_id"],
        "configured": bool(ns["url"]),
        "ready": bool(ns["url"]) and det["key_set"],
        "last_push": last["text"],
        "last_push_ok": last["ok"],
        "last_push_http": last["http"],
        "last_push_url": mask_url(last["url"], channel) if last["url"] else "",
    }


# ---- 测试结果判定（服务端正文也要看）---------------------------------------

def extract_json(text):
    """从文本里抠出第一个完整的 JSON 对象（含嵌套），解析失败返回 None。"""
    i = text.find("{")
    while i >= 0:
        depth, in_str, esc = 0, False, False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except ValueError:
                        break
        i = text.find("{", i + 1)
    return None


def _json_field(text, name):
    m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % name, text)
    if m:
        try:
            return json.loads('"%s"' % m.group(1))
        except ValueError:
            return m.group(1)
    return ""


def server_error(payload, text, channel):
    """服务端业务错误 -> 人话；没有错误返回 None。

    payload 解析不出来时（正文被截断等）退回正则扫 code/errcode。
    """
    if isinstance(payload, dict):
        if channel == "serverchan":
            code = payload.get("code")
            if code not in (0, "0", None):
                return "Server酱 返回错误：%s（code=%s）" % (
                    payload.get("message") or payload.get("info") or "未知错误", code)
        if channel == "wecom":
            code = payload.get("errcode")
            if code not in (0, "0", None):
                return "企业微信 返回错误：%s（errcode=%s）" % (
                    payload.get("errmsg") or "未知错误", code)
        for k in ("ok", "success"):
            if payload.get(k) is False:
                return "服务端返回失败：%s" % json.dumps(payload, ensure_ascii=False)[:300]
        if payload.get("error"):
            return "服务端返回错误：%s" % payload["error"]
        return None
    if channel == "serverchan":
        m = re.search(r'"code"\s*:\s*(-?\d+)', text)
        if m and m.group(1) != "0":
            return "Server酱 返回错误：%s（code=%s）" % (_json_field(text, "message") or "未知错误",
                                                     m.group(1))
    if channel == "wecom":
        m = re.search(r'"errcode"\s*:\s*(-?\d+)', text)
        if m and m.group(1) != "0":
            return "企业微信 返回错误：%s（errcode=%s）" % (_json_field(text, "errmsg") or "未知错误",
                                                       m.group(1))
    return None


def judge(channel, rc, msg):
    """(ok, 人话)。

    HTTP 通了但服务端正文报错也算失败；正文能解析时给一句人话 + 原始 JSON，
    解析不出来时原样把 notify-test 的输出带回去（服务端原文一定不丢）。
    """
    payload = extract_json(msg)
    err = server_error(payload, msg, channel)
    if err:
        # json.dumps(ensure_ascii=False)：Server酱 的正文里中文是 \uXXXX 转义的，
        # 这里重新序列化一次，页面上就能看到「错误的Key」而不是一串 \u9519\u8bef；
        # 正文被截断（解析不出 JSON）时退回原始文本，原文一样不丢。
        raw = json.dumps(payload, ensure_ascii=False)[:400] if payload is not None else msg[:400]
        return False, "%s\n服务端原文：%s" % (err, raw)
    if rc != 0:
        return False, (msg or "powerfee notify-test 失败（详见 powerfee log）")
    return True, (msg or "测试消息已发送")


# ---- 写配置 + 测试 ----------------------------------------------------------

def apply_and_test(channel, pairs, deletes=(), extra=None):
    """写 notify 配置 → commit → 发测试消息 → 回报（含服务端原文）。"""
    ok, err = uci_apply([("powerfee.notify", "notify")] + pairs, deletes)
    if not ok:
        return fail(err, channel=channel)
    ns = notify_state()
    if not ns["url"]:
        return fail("配置写入后 notify.url 还是空的，请检查 uci 权限", channel=channel)
    if ns["enabled"] is not True:
        return fail("配置写入后 notify.enabled 不是 1，请检查 uci 权限", channel=channel)
    rc, msg = notify_test()
    passed, detail = judge(channel, rc, msg)
    res = {
        "ok": passed,
        "channel": channel,
        "channel_name": CHANNEL_NAMES.get(channel, channel),
        "url_masked": mask_url(ns["url"], channel),
        "message": detail[:400],
        "last_push": last_push_line(),
    }
    if extra:
        res.update(extra)
    if not passed:
        res["error"] = detail[:400]
    return out_json(res, 0 if passed else 1)


# ---- 子命令 -----------------------------------------------------------------

def cmd_status(args):
    return out_json(status_obj())


def cmd_set_serverchan(args):
    key = (args.sendkey or "").strip()
    url, err = serverchan_url(key)
    if err:
        return fail(err, channel="serverchan")
    pairs = [
        ("powerfee.notify.enabled", "1"),
        ("powerfee.notify.url", url),
        ("powerfee.notify.method", "POST"),
        ("powerfee.notify.content_type", "application/json"),
        ("powerfee.notify.body", SERVERCHAN_BODY),
        ("powerfee.notify.timeout", "15"),
    ]
    # Server酱用 URL 里的 key 鉴权，不留任何令牌头（否则会把旧 token 发给它）
    return apply_and_test("serverchan", pairs, deletes=["powerfee.notify.token",
                                                        "powerfee.notify.token_header"])


def cmd_set_wecom(args):
    url = (args.url or "").strip()
    if not url:
        return fail("请填写企业微信群机器人的 Webhook 地址", channel="wecom")
    if not url.lower().startswith(("http://", "https://")):
        return fail("Webhook 地址要以 http:// 或 https:// 开头：%s" % url, channel="wecom")
    if not wecom_key_of(url) and not is_placeholder_key(url):
        return fail("这个地址里没有 ?key=… 参数，像是复制少了（企业微信机器人的地址形如 "
                    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx）",
                    channel="wecom")
    pairs = [
        ("powerfee.notify.enabled", "1"),
        ("powerfee.notify.url", url),
        ("powerfee.notify.method", "POST"),
        ("powerfee.notify.content_type", "application/json"),
        ("powerfee.notify.body", WECOM_BODY),
        ("powerfee.notify.timeout", "15"),
    ]
    return apply_and_test("wecom", pairs, deletes=["powerfee.notify.token",
                                                   "powerfee.notify.token_header"])


def cmd_set_webhook(args):
    url = (args.url or "").strip()
    if not url:
        return fail("请填写 webhook 地址", channel="webhook")
    if not url.lower().startswith(("http://", "https://")):
        return fail("webhook 地址要以 http:// 或 https:// 开头：%s" % url, channel="webhook")
    method = (args.method or "POST").strip().upper() or "POST"
    if method not in ("POST", "GET"):
        return fail("method 只支持 POST 或 GET（收到的是 %s）" % method, channel="webhook")
    ctype = (args.content_type or "application/json").strip() or "application/json"
    # GET 时 body 模板同样有用：渲染结果会被百分号编码后拼到 URL 后面（见 notify_send）
    body = args.body if args.body is not None and args.body.strip() != "" else WEBHOOK_BODY
    pairs = [
        ("powerfee.notify.enabled", "1"),
        ("powerfee.notify.url", url),
        ("powerfee.notify.method", method),
        ("powerfee.notify.content_type", ctype),
        ("powerfee.notify.body", body),
        ("powerfee.notify.timeout", "15"),
    ]
    # 自定义通道「原样写入」：不动 token / token_header（要鉴权就在设置页里配）
    return apply_and_test("webhook", pairs)


def cmd_set_clawbot(args):
    svc, bot_id, bot, err = clawbot_pick(args.bot)
    if err:
        return fail(err, channel="clawbot", bot_id=bot_id or "")
    url, pairs = clawbot_uci(bot_id, (bot.get("api_token") or "").strip(), svc.port)
    return apply_and_test("clawbot", pairs, extra={"bot_id": bot_id, "mode": svc.mode})


def cmd_off(args):
    ok, err = uci_apply([("powerfee.notify.enabled", "0")])
    if not ok:
        return fail(err)
    ns = notify_state()
    channel = detect_channel(ns["url"])
    return out_json({
        "ok": True,
        "channel": channel,
        "channel_name": CHANNEL_NAMES.get(channel, channel),
        "enabled": False,
        "message": "推送已关闭（notify.enabled=0）；通道配置保留，随时可以重新启用",
    })


def cmd_test(args):
    ns = notify_state()
    channel = detect_channel(ns["url"])
    if not ns["url"]:
        return fail("还没配置推送通道：先在页面顶部选一个通道并填好参数", channel=channel)
    if not ns["enabled"]:
        return fail("推送当前是关闭的（notify.enabled=0）："
                    "先点通道里的「启用并测试」，或用 powerfee push set-* 重新启用",
                    channel=channel)
    rc, msg = notify_test()
    passed, detail = judge(channel, rc, msg)
    res = {
        "ok": passed,
        "channel": channel,
        "channel_name": CHANNEL_NAMES.get(channel, channel),
        "url_masked": mask_url(ns["url"], channel),
        "message": detail[:400],
        "last_push": last_push_line(),
    }
    if not passed:
        res["error"] = detail[:400]
    return out_json(res, 0 if passed else 1)


# ---- 入口 -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="powerfee push channel manager")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("status", help="输出 JSON 状态（只回掩码，不回密钥）")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("set-serverchan", help="切到 Server酱：写配置 + commit + 发测试")
    p.add_argument("sendkey", nargs="?", help="Server酱 SendKey")
    p.set_defaults(func=cmd_set_serverchan)

    p = sub.add_parser("set-wecom", help="切到企业微信群机器人：写配置 + commit + 发测试")
    p.add_argument("url", nargs="?", help="群机器人 Webhook 地址")
    p.set_defaults(func=cmd_set_wecom)

    p = sub.add_parser("set-webhook", help="自定义 webhook：写配置 + commit + 发测试")
    p.add_argument("url", nargs="?", help="webhook 地址")
    p.add_argument("method", nargs="?", help="POST（默认）或 GET")
    p.add_argument("content_type", nargs="?", help="POST 的 Content-Type，默认 application/json")
    p.add_argument("body", nargs="?", help='请求体模板，默认 {"text":"{text}"}')
    p.set_defaults(func=cmd_set_webhook)

    p = sub.add_parser("set-clawbot", help="切回 ClawBot 通道：写配置 + commit + 发测试")
    p.add_argument("--bot", help="指定 bot_id（默认自动选已激活的那个）")
    p.set_defaults(func=cmd_set_clawbot)

    p = sub.add_parser("off", help="关掉推送（notify.enabled=0，配置保留）")
    p.set_defaults(func=cmd_off)

    p = sub.add_parser("test", help="只发一条测试消息，不改配置")
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
