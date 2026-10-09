#!/usr/bin/env python3
"""真机测试：通知推送（webhook）与查询端点（CGI）。

用法（Windows cmd，注意 set "VAR=值" 的引号写法）：
    set "POWERFEE_HOST=192.168.1.1"
    set "POWERFEE_PASS=你的路由器密码"
    python tools\\test_notify.py

脚本做四件事：
  1) 上传最新 powerfee 主程序与 /www/cgi-bin/powerfee 查询端点，跑语法检查与自检；
  2) 在路由器上起一个假 webhook 服务器（python3），验证推送内容、模板占位符、
     token 请求头、GET 方式、失败不静默；并用真实接口触发
     low / repeat-low / warn / recovered / stale / stale-ok / force / test
     全部 reason（断言「与邮件同源」，且 mail.enabled=0 时照推）；
  3) 验证查询端点：token 校验、开关、白名单、各子命令、POST 表单、越权路径；
  4) 收尾：恢复 /etc/config/powerfee（测试前已备份到 /root/powerfee.conf.mine）
     与 /etc/powerfee，关掉 notify 与 mail、清空房间、重启服务。

⚠️ 会临时改路由器配置与状态（会恢复）；跑之前确认这台机器可以折腾。
"""
import argparse
import io
import json
import os
import posixpath
import re
import sys
import tempfile
import time
import urllib.parse

import paramiko

HOST = os.environ.get("POWERFEE_HOST", "192.168.1.1")
USER = os.environ.get("POWERFEE_USER", "root")
PASSWORD = os.environ.get("POWERFEE_PASS", "")
_here = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("POWERFEE_BASE") or os.path.dirname(_here)  # tools/ 的父目录 = 仓库根
STAGE = "/tmp/pf_stage"
HOOK_DIR = "/tmp/pf_hook"
HOOK_PORT = 8765
HTTP_PORT = 8443
CONF_BAK = "/root/powerfee.conf.mine"
STATE_BAK = "/root/pf_state.bak"
RESULTS = []
SSH = {"c": None}

# 假 webhook 服务器：收到的每个请求按 JSON Lines 落盘，/fail 路径返回 500
FAKE_WEBHOOK_PY = r'''#!/usr/bin/env python3
"""假 webhook 服务器：把收到的请求（方法/路径/头/正文）逐条写成 JSON Lines。
用法：python3 fake_webhook.py --port 8765 --outdir /tmp/pf_hook
路径 /fail 返回 500，其余返回 200 {"ok":true}。
"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        rec = {"method": self.command, "path": self.path,
               "headers": {k: v for k, v in self.headers.items()}, "body": body}
        os.makedirs(ARGS.outdir, exist_ok=True)
        with open(os.path.join(ARGS.outdir, "recv.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.path.split("?")[0] == "/fail":
            payload = b'{"ok":false,"err":"boom"}'
            self.send_response(500)
        else:
            payload = b'{"ok":true}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, fmt, *args):
        sys.stderr.write(fmt % args + "\n")


ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8765)
ap.add_argument("--outdir", default="/tmp/pf_hook")
ARGS = ap.parse_args()
ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()
'''


# ---------------------------------------------------------------- 基础工具

def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""))
    sys.stdout.flush()


def section(title):
    print("\n== %s" % title)
    sys.stdout.flush()


def connect():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=25,
                allow_agent=False, look_for_keys=False)
    SSH["c"] = ssh
    return ssh


def run(cmd, timeout=180, retries=4):
    """执行命令；校园网抖动会掐断 SSH，这里自动重连重试。"""
    last = None
    for attempt in range(retries):
        try:
            if SSH["c"] is None:
                connect()
            stdin, stdout, stderr = SSH["c"].exec_command(cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            rc = stdout.channel.recv_exit_status()
            return rc, out, err
        except Exception as exc:  # noqa: BLE001
            last = exc
            print("   ! 连接异常（第 %d 次）：%s，3 秒后重连" % (attempt + 1, exc))
            try:
                SSH["c"].close()
            except Exception:
                pass
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("命令重试后仍失败：%s（%s）" % (cmd, last))


def upload(local, remote, mode=0o755):
    data = io.open(local, "rb").read()
    tmp = remote + ".new"
    for attempt in range(4):
        try:
            run("mkdir -p %s" % posixpath.dirname(remote))
            stdin, stdout, stderr = SSH["c"].exec_command(
                "cat > %s && chmod %o %s && mv -f %s %s && echo UP_OK" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode("utf-8", "replace")
            if "UP_OK" not in out:
                raise RuntimeError("rc!=0: %s" % out)
            return len(data)
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传失败（第 %d 次）：%s" % (attempt + 1, exc))
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("上传失败：%s" % remote)


def pick_test_room():
    room = os.environ.get("POWERFEE_TEST_ROOM", "")
    if room:
        return room
    rc, out, _ = run("powerfee json rooms '' 1")
    try:
        return json.loads(out)["rooms"][0]["room"]
    except Exception:
        return ""


def hook_records():
    rc, out, _ = run("cat %s/recv.jsonl 2>/dev/null" % HOOK_DIR)
    recs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass
    return recs


def hook_clear():
    run("rm -f %s/recv.jsonl" % HOOK_DIR)


def hook_json(n=1):
    """返回最后 n 条记录的正文（解析成 dict）；n<=0 表示全部。"""
    recs = hook_records()
    if n > 0:
        recs = recs[-n:]
    out = []
    for r in recs:
        try:
            out.append(json.loads(r["body"]))
        except Exception:
            out.append(None)
    return out


def http_req(qs, post=False, timeout=60):
    """打一次查询端点，返回 (http_code, headers_text, body)。"""
    url = "https://127.0.0.1:%d/cgi-bin/powerfee" % HTTP_PORT
    if post:
        cmd = ("curl -k -s -m %d -D /tmp/pf_hdr.txt -o /tmp/pf_body.txt -w '%%{http_code}' "
               "-X POST -d '%s' '%s'" % (timeout, qs, url))
    else:
        cmd = ("curl -k -s -m %d -D /tmp/pf_hdr.txt -o /tmp/pf_body.txt -w '%%{http_code}' "
               "'%s?%s'" % (timeout, url, qs))
    rc, out, err = run(cmd)
    code = out.strip().splitlines()[-1] if out.strip() else ""
    _, hdr, _ = run("cat /tmp/pf_hdr.txt 2>/dev/null")
    _, body, _ = run("cat /tmp/pf_body.txt 2>/dev/null")
    return code, hdr, body


def hdr_get(hdr, name):
    for line in hdr.splitlines():
        if ":" in line and line.split(":", 1)[0].strip().lower() == name.lower():
            return line.split(":", 1)[1].strip()
    return ""


def rec_get(rec, name):
    for k, v in rec.get("headers", {}).items():
        if k.lower() == name.lower():
            return v
    return ""


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true", help="跳过上传（只跑测试）")
    args = ap.parse_args()

    connect()
    print("== 已连接 %s" % HOST)

    try:
        # ---------------- 1. 准备 ----------------
        section("1) 上传与语法检查")
        if not args.no_upload:
            for local, remote in [("files/usr/bin/powerfee", "/usr/bin/powerfee"),
                                  ("files/www/cgi-bin/powerfee", "/www/cgi-bin/powerfee"),
                                  ("tools/fake_smtp.py", STAGE + "/fake_smtp.py")]:
                n = upload(os.path.join(BASE, local.replace("/", os.sep)), remote)
                print("   上传 %-38s -> %-32s (%d 字节)" % (local, remote, n))
            tmpweb = os.path.join(tempfile.gettempdir(), "fake_webhook.py")
            with io.open(tmpweb, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(FAKE_WEBHOOK_PY)
            upload(tmpweb, STAGE + "/fake_webhook.py")
            record("上传主程序 / CGI / 假服务器", True, "")
        rc, out, err = run("sh -n /usr/bin/powerfee && sh -n /www/cgi-bin/powerfee && echo OK")
        record("sh -n 语法检查（主程序 + CGI）", "OK" in out, err.strip()[:200])
        rc, out, err = run("powerfee selftest")
        ok = rc == 0 and "全部通过" in out
        record("powerfee selftest 仍全过", ok, out.strip().splitlines()[-1] if out.strip() else err[:200])

        # 1.2.0 命名账号：只读检查（不动任何配置）
        rc, out, err = run("powerfee json mail-accounts")
        try:
            _ja = json.loads(out)
        except Exception:
            _ja = {}
        rc2, out2, err2 = run("powerfee json push-accounts")
        try:
            _jb = json.loads(out2)
        except Exception:
            _jb = {}
        record("json mail-accounts / push-accounts 返回合法 JSON（密钥只回掩码/是否已设置）",
               _ja.get("ok") is True and isinstance(_ja.get("accounts"), list)
               and _jb.get("ok") is True and isinstance(_jb.get("accounts"), list),
               "mail=%d push=%d" % (len(_ja.get("accounts") or []), len(_jb.get("accounts") or [])))
        rc, out, err = run("powerfee json room-list")
        try:
            _jl = json.loads(out)
        except Exception:
            _jl = {}
        _r0 = (_jl.get("rooms") or [{}])[0]
        record("json room-list 的房间对象带发件账号 / 推送账号 / 通道字段",
               "mail_account" in _r0 and "mail_account_effective" in _r0
               and "notify_account" in _r0 and "notify_channel" in _r0,
               "keys=%s" % sorted(k for k in _r0 if "account" in k or "channel" in k))

        run("cp -f /etc/config/powerfee %s" % CONF_BAK)
        run("rm -rf %s && mkdir -p %s && cp -a /etc/powerfee/. %s/ 2>/dev/null" % (STATE_BAK, STATE_BAK, STATE_BAK))
        print("   已备份配置到 %s，状态目录到 %s" % (CONF_BAK, STATE_BAK))

        # 停服务：避免守护进程在测试期间自己查询、抢状态
        run("/etc/init.d/powerfee stop 2>/dev/null; sleep 1; /etc/init.d/powerfee status 2>&1 || true")

        section("2) 起假 webhook 服务器")
        run("ps w | grep fake_webhook | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null; "
            "rm -rf %s && mkdir -p %s" % (HOOK_DIR, HOOK_DIR))
        run("cd /tmp && (python3 %s/fake_webhook.py --port %d --outdir %s "
            "</dev/null >%s/server.log 2>&1 &)" % (STAGE, HOOK_PORT, HOOK_DIR, HOOK_DIR))
        time.sleep(2)
        rc, out, _ = run("netstat -ltn | grep -c ':%d '" % HOOK_PORT)
        record("假 webhook 已监听 :%d" % HOOK_PORT, out.strip() == "1",
               "监听数=%s；日志：%s" % (out.strip(), run("cat %s/server.log" % HOOK_DIR)[1].strip()[:200]))

        # ---------------- 2. 基础推送 ----------------
        section("3) 基础推送与模板渲染")
        run("uci -q get powerfee.notify >/dev/null 2>&1 || uci set powerfee.notify=notify; "
            "uci set powerfee.notify.enabled=1; "
            "uci set 'powerfee.notify.url=http://127.0.0.1:%d/hook'; "
            "uci set powerfee.notify.method=POST; "
            "uci set powerfee.notify.content_type=application/json; "
            "uci set 'powerfee.notify.body={\"text\":\"{text}\"}'; "
            "uci set powerfee.notify.token=''; uci set powerfee.notify.token_header=Authorization; "
            "uci set powerfee.notify.timeout=5; uci set powerfee.notify.http_enabled=0; "
            "uci set powerfee.notify.http_token=''; uci commit powerfee" % HOOK_PORT)

        TEST_ROOM = pick_test_room()
        rc, out, _ = run("powerfee json set-room %s" % TEST_ROOM)
        print("   测试房间：%s（set-room %s）" % (TEST_ROOM, "OK" if '"ok":true' in out else "?"))
        run("powerfee check")
        rc, st, _ = run("powerfee json status")
        try:
            ST = json.loads(st)
        except Exception:
            ST = {}

        hook_clear()
        rc, out, err = run("powerfee notify-test; echo rc=$?")
        recs = hook_records()
        bodies = hook_json(1)
        body = bodies[0] if bodies else None
        # 测试消息带上当前状态：前缀 + 房间名 + 与状态一致的余额（1.0.3 起）
        text = (body or {}).get("text", "")
        m_bal = re.search(r"剩余 ([0-9.eE+-]+)", text)
        try:
            bal_ok = bool(m_bal) and abs(float(m_bal.group(1)) - float(ST.get("balance"))) < 1e-9
        except (TypeError, ValueError):
            bal_ok = False
        room_ok = (not ST.get("room_display")) or (ST.get("room_display") in text)
        ok = ("rc=0" in out and "测试通知已推送" in out and len(recs) == 1
              and recs[0]["method"] == "POST" and recs[0]["path"] == "/hook"
              and text.startswith("🔔 测试通知：宿舍电量哨兵（OpenWrt 版）｜")
              and room_ok and bal_ok)
        record("notify-test 推送成功（POST /hook，含房间与余额）", bool(ok),
               "收到 %d 条，body=%s" % (len(recs), json.dumps(body, ensure_ascii=False) if body else "无"))
        record("Content-Type 请求头正确", rec_get(recs[0], "Content-Type") == "application/json" if recs else False,
               rec_get(recs[0], "Content-Type") if recs else "")

        # 全占位符模板
        body_tpl = ('{"text":"{text}","title":"{title}","room":"{room}","balance":"{balance}",'
                    '"unit":"{unit}","level":"{level}","reason":"{reason}","daily":"{daily}",'
                    '"days_left":"{days_left}","time":"{time}","device":"{device}","nope":"{nope}"}')
        run("uci set 'powerfee.notify.body=%s'; uci commit powerfee" % body_tpl)
        hook_clear()
        run("powerfee notify-test")
        bodies = hook_json(1)
        d = bodies[0] if bodies else None
        detail = json.dumps(d, ensure_ascii=False) if d else "无"
        # 逐项断言，失败时能看清是哪一项
        def same_num(a, b):
            try:
                return abs(float(a) - float(b)) < 1e-9
            except (TypeError, ValueError):
                return False

        checks = [
            ("reason=test", d and d.get("reason") == "test"),
            ("level 与状态一致", d and d.get("level") == ST.get("level", "ok")),
            ("unit=度", d and d.get("unit") == "度"),
            ("room 与状态一致", d and d.get("room") == ST.get("room_display", "")),
            ("balance 与状态一致", d and same_num(d.get("balance"), ST.get("balance"))),
            ("time 形如日期", d and len(str(d.get("time", ""))) >= 19 and str(d.get("time"))[4] == "-"),
            ("device 非空且无尾随空格", d and bool(d.get("device")) and d["device"] == d["device"].strip()),
            ("daily/days_left 字段存在", d and "daily" in d and "days_left" in d),
            ("未知占位符原样保留", d and d.get("nope") == "{nope}"),
            ("title 非空", d and bool(d.get("title"))),
        ]
        bad = [n for n, okk in checks if not okk]
        record("全占位符模板渲染正确（中文不乱码）", not bad, ("全部正确" if not bad else "失败项：" + ", ".join(bad)) + " | " + detail[:400])

        # token 请求头
        run("uci set powerfee.notify.token='pf-token-123'; "
            "uci set powerfee.notify.token_header='X-PF-Token'; uci commit powerfee")
        hook_clear()
        run("powerfee notify-test")
        recs = hook_records()
        got = rec_get(recs[0], "X-PF-Token") if recs else ""
        record("token 放进指定请求头（X-PF-Token 原样）", got == "pf-token-123", "收到：%r" % got)
        run("uci set powerfee.notify.token=''; uci set powerfee.notify.token_header=Authorization; uci commit powerfee")

        # GET 方式
        run("uci set powerfee.notify.method=GET; "
            "uci set 'powerfee.notify.url=http://127.0.0.1:%d/hook?src=pf'; "
            "uci set 'powerfee.notify.body={\"text\":\"{text}\"}'; uci commit powerfee" % HOOK_PORT)
        hook_clear()
        rc, out, err = run("powerfee notify-test; echo rc=$?")
        recs = hook_records()
        decoded = urllib.parse.unquote(recs[0]["path"]) if recs else ""
        # 1.0.3 起测试消息带房间与余额（GET 方式的 query 里同样要能看到）
        m2 = re.search(r"剩余 ([0-9.eE+-]+)", decoded)
        try:
            bal_ok2 = bool(m2) and abs(float(m2.group(1)) - float(ST.get("balance"))) < 1e-9
        except (TypeError, ValueError):
            bal_ok2 = False
        ok = ("rc=0" in out and len(recs) == 1 and recs[0]["method"] == "GET"
              and decoded.startswith("/hook?src=pf&")
              and '{"text":"🔔 测试通知：宿舍电量哨兵（OpenWrt 版）｜' in decoded
              and (not ST.get("room_display") or ST.get("room_display") in decoded)
              and bal_ok2)
        record("GET 方式：渲染结果（百分号编码）拼在 url 后面", ok,
               "path=%s" % (recs[0]["path"][:200] if recs else "无（rc=%s err=%s）" % (out.strip(), err.strip()[:120])))
        run("uci set powerfee.notify.method=POST; "
            "uci set 'powerfee.notify.url=http://127.0.0.1:%d/hook'; uci commit powerfee" % HOOK_PORT)

        # 关闭开关
        hook_clear()
        rc, out, err = run("uci set powerfee.notify.enabled=0; uci commit powerfee; "
                           "powerfee notify-test; echo rc=$?; "
                           "uci set powerfee.notify.enabled=1; uci commit powerfee")
        record("notify.enabled=0 时不推送且明确报错",
               "rc=1" in out and "通知未启用" in (out + err) and len(hook_records()) == 0,
               (out + err).strip().replace("\n", " | ")[:200])

        # ---------------- 3. 各 reason 与邮件同源 ----------------
        section("4) 全部 reason 都推（mail.enabled=0 的组合）")
        run("uci set 'powerfee.notify.body={\"text\":\"{text}\",\"reason\":\"{reason}\",\"level\":\"{level}\"}'; "
            "uci set powerfee.mail.enabled=0; uci set powerfee.main.cooldown=180; "
            "uci set powerfee.main.threshold=20; uci commit powerfee")
        hook_clear()

        def reasons_now():
            return [d.get("reason") for d in hook_json(0) if d]

        run("powerfee notify-test")                                     # test
        run("powerfee check --force")                                   # force
        run("uci set powerfee.main.threshold=9999; uci commit powerfee; powerfee check")   # ok->low
        run("uci set powerfee.main.cooldown=0; uci commit powerfee; powerfee check")       # repeat-low
        run("uci set powerfee.main.threshold=20; uci set powerfee.main.cooldown=180; uci commit powerfee; powerfee check")  # recovered
        # stale：把 last_ok_at 拨到 7 小时前，并把接口指向一个连不上的地址
        rc, so, se = run("NOW=$(date +%s); OLD=$((NOW-7*3600)); "
            "sed -i \"s/^last_ok_at=.*/last_ok_at=$OLD/\" /etc/powerfee/state; "
            "grep -q '^last_ok_at=' /etc/powerfee/state || echo \"last_ok_at=$OLD\" >>/etc/powerfee/state; "
            "uci set 'powerfee.api.url=http://127.0.0.1:1/nope'; uci commit powerfee; powerfee check; echo rc=$?")
        record("stale 场景：接口不通时 check 失败（触发监控失效告警）", "rc=1" in so,
               (so + se).strip().replace("\n", " | ")[:200])
        # stale-ok：恢复接口（url 从测试前的备份里读回来）
        ORIG_URL = None
        rc, out, _ = run("sed -n \"s/^[[:space:]]*option url //p\" %s" % CONF_BAK)
        for line in out.splitlines():
            line = line.strip().strip("'")
            if line.startswith("http"):
                ORIG_URL = line
        if ORIG_URL:
            run("uci set powerfee.api.url='%s'; uci commit powerfee; powerfee check; echo rc=$?" % ORIG_URL)
        else:
            record("恢复 api.url（从备份读取）", False, "备份里没找到 url")
        run("uci set powerfee.main.threshold=40; uci commit powerfee; powerfee check")  # ok->warn

        got = reasons_now()
        want = ["test", "force", "ok->low", "repeat-low", "recovered", "stale", "stale-ok", "ok->warn"]
        missing = [w for w in want if w not in got]
        record("8 个 reason 全部推送到位", not missing,
               "收到 %d 条：%s%s" % (len(got), ",".join(got), ("；缺 " + ",".join(missing)) if missing else ""))
        rc, lg, _ = run("powerfee log 200 | grep -c '邮件未启用，跳过发送'")
        record("mail.enabled=0 时通知照推（邮件同源、互不影响）", int(lg.strip() or 0) >= 3,
               "「邮件未启用」日志 %s 条" % lg.strip())

        # ---------------- 4. 失败路径 ----------------
        section("5) 失败路径与邮件通道不受影响")
        run("uci set powerfee.main.threshold=20; uci commit powerfee")
        run("uci set 'powerfee.notify.url=http://127.0.0.1:9/closed'; uci commit powerfee")
        hook_clear()
        rc, out, err = run("powerfee notify-test; echo rc=$?")
        _, lg, _ = run("powerfee log 20")
        record("连不通时 notify-test 失败并写明原因",
               "rc=1" in out and "推送失败" in (out + err) and "通知推送失败" in lg and "curl 退出码" in lg,
               [l for l in lg.splitlines() if "通知推送失败" in l][-1:][0][:200] if lg else "")

        run("uci set 'powerfee.notify.url=http://127.0.0.1:%d/fail'; uci commit powerfee" % HOOK_PORT)
        rc, out, err = run("powerfee notify-test; echo rc=$?")
        _, lg, _ = run("powerfee log 20")
        record("HTTP 500 时失败并写明状态码",
               "rc=1" in out and "HTTP 500" in lg,
               [l for l in lg.splitlines() if "通知推送失败" in l][-1:][0][:200] if lg else "")

        # 邮件通道不受影响：起假 SMTP（plain），notify 指向坏地址，check --force 应仍发邮件
        run("ps w | grep fake_smtp | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null; "
            "rm -rf /tmp/fs && mkdir -p /tmp/fs/plain")
        run("cd /tmp && (python3 %s/fake_smtp.py --port 2525 --mode plain --outdir /tmp/fs/plain "
            "</dev/null >/tmp/fs/plain.log 2>&1 &)" % STAGE)
        time.sleep(2)
        run("uci set powerfee.mail.enabled=1; uci set powerfee.mail.transport=auto; "
            "uci set powerfee.mail.host='127.0.0.1'; uci set powerfee.mail.port='2525'; "
            "uci set powerfee.mail.security='none'; uci set powerfee.mail.user=''; "
            "uci set powerfee.mail.password=''; uci set powerfee.mail.to='owner@example.com'; "
            "uci set powerfee.mail.tls_verify='0'; uci commit powerfee")
        rc, out, err = run("powerfee check --force; echo rc=$?")
        time.sleep(1)
        rc2, n_eml, _ = run("ls /tmp/fs/plain/*.eml 2>/dev/null | wc -l")
        _, lg, _ = run("powerfee log 20")
        ok = ("rc=0" in out and n_eml.strip() == "1"
              and "通知推送失败" in lg and "邮件已发送" in lg)
        record("notify 失败不影响邮件（坏 webhook + 真发信）", ok,
               "eml=%s 封；日志尾：%s" % (n_eml.strip(),
                                        " | ".join([l for l in lg.splitlines() if "通知" in l or "邮件" in l][-2:])[:240]))
        run("uci set powerfee.mail.enabled=0; uci commit powerfee")

        # ---------------- 5. 查询端点 ----------------
        section("6) 查询端点 /cgi-bin/powerfee")
        run("uci set powerfee.notify.http_enabled=1; uci set powerfee.notify.http_token='pf-http-token-9'; "
            "uci commit powerfee")
        TOKEN = "pf-http-token-9"

        code, hdr, body = http_req("token=%s&cmd=status" % TOKEN)
        try:
            j = json.loads(body)
        except Exception:
            j = None
        record("cmd=status 返回 200 + 合法 JSON",
               code == "200" and j is not None and "balance" in j and "level" in j,
               "HTTP %s，%s" % (code, body.strip()[:160]))
        record("响应头 Content-Type / Cache-Control 正确",
               hdr_get(hdr, "Content-Type") == "application/json; charset=utf-8"
               and hdr_get(hdr, "Cache-Control") == "no-store",
               "CT=%r CC=%r" % (hdr_get(hdr, "Content-Type"), hdr_get(hdr, "Cache-Control")))

        code, hdr, body = http_req("token=%s&cmd=brief" % TOKEN)
        try:
            jb = json.loads(body)
        except Exception:
            jb = None
        ok = (code == "200" and jb and jb.get("ok") is True and isinstance(jb.get("text"), str)
              and jb["text"] and "\n" not in jb["text"]
              and str(jb.get("balance")) == str(ST.get("balance", ""))
              and jb.get("level") == ST.get("level", "ok"))
        record("cmd=brief 返回单行摘要", bool(ok), body.strip()[:220])

        code, _, body = http_req("token=WRONG&cmd=status")
        record("错 token -> 403 bad token", code == "403" and json.loads(body or "{}").get("error") == "bad token",
               "HTTP %s %s" % (code, body.strip()[:120]))

        run("uci set powerfee.notify.http_enabled=0; uci commit powerfee")
        code, _, body = http_req("token=%s&cmd=status" % TOKEN)
        record("http_enabled=0 -> 403 endpoint disabled",
               code == "403" and json.loads(body or "{}").get("error") == "endpoint disabled",
               "HTTP %s %s" % (code, body.strip()[:120]))
        run("uci set powerfee.notify.http_enabled=1; uci set powerfee.notify.http_token=''; uci commit powerfee")
        code, _, body = http_req("token=%s&cmd=status" % TOKEN)
        record("http_token 为空 -> 403 endpoint disabled",
               code == "403" and json.loads(body or "{}").get("error") == "endpoint disabled",
               "HTTP %s %s" % (code, body.strip()[:120]))
        run("uci set powerfee.notify.http_token='%s'; uci commit powerfee" % TOKEN)

        for label, qs in [
            ("路径穿越 cmd=../../bin/sh", "token=%s&cmd=../../bin/sh" % TOKEN),
            ("URL 编码的穿越 cmd=%2e%2e%2f%2e%2e%2fbin%2fsh", "token=%s&cmd=%%2e%%2e%%2f%%2e%%2e%%2fbin%%2fsh" % TOKEN),
            ("不在白名单的 cmd=set-room", "token=%s&cmd=set-room" % TOKEN),
            ("缺失 cmd", "token=%s" % TOKEN),
            ("大小写不同 cmd=CHECK", "token=%s&cmd=CHECK" % TOKEN),
        ]:
            code, _, body = http_req(qs)
            record("%s -> 400 bad cmd" % label,
                   code == "400" and json.loads(body or "{}").get("error") == "bad cmd",
                   "HTTP %s %s" % (code, body.strip()[:120]))

        code, _, body = http_req("token=%s&cmd=groups" % TOKEN)
        try:
            jg = json.loads(body)
        except Exception:
            jg = None
        record("cmd=groups 返回 JSON", code == "200" and jg is not None and "groups" in jg,
               body.strip()[:160])

        code, _, body = http_req("token=%s&cmd=rooms&kw=%s&limit=3" % (TOKEN, TEST_ROOM))
        try:
            jr = json.loads(body)
        except Exception:
            jr = None
        record("cmd=rooms 支持 kw/limit", code == "200" and jr and jr.get("shown", 9) <= 3 and jr.get("total", 0) >= 1,
               "shown=%s total=%s" % (jr.get("shown") if jr else "?", jr.get("total") if jr else "?"))

        # 中文关键词（URL 编码）——验证 CGI 的 %XX 解码按字节还原 UTF-8。
        # 关键词不写死：从接口返回的校区/楼栋名里动态取一个含中文的，保证一定能命中房间。
        _, gbody, _ = http_req("token=%s&cmd=groups" % TOKEN)
        try:
            _groups = (json.loads(gbody) or {}).get("groups") or []
        except Exception:
            _groups = []
        KW = ""
        for _g in _groups:
            _cands = ([str(_g.get("campus"))] if _g.get("campus") else []) + \
                     [str(_b) for _b in (_g.get("buildings") or [])]
            for _cand in _cands:
                if any("\u4e00" <= _ch <= "\u9fff" for _ch in _cand):
                    KW = _cand
                    break
            if KW:
                break
        code, _, body = http_req("token=%s&cmd=rooms&kw=%s&limit=2" % (TOKEN, urllib.parse.quote(KW)))
        try:
            jz = json.loads(body)
        except Exception:
            jz = None
        record("CGI 的 URL 解码支持中文关键词（kw=%s，取自接口数据）" % KW,
               bool(KW) and code == "200" and jz and jz.get("shown", 0) >= 1 and jz.get("total", 0) >= 1,
               "shown=%s total=%s" % (jz.get("shown") if jz else "?", jz.get("total") if jz else "?"))

        code, _, body = http_req("token=%s&cmd=history&n=3" % TOKEN)
        try:
            jh = json.loads(body)
        except Exception:
            jh = None
        record("cmd=history 支持 n", code == "200" and jh and len(jh.get("points", [])) <= 3 and jh.get("total", 0) >= 1,
               body.strip()[:200])

        code, _, body = http_req("token=%s&cmd=log&n=5" % TOKEN)
        try:
            jl = json.loads(body)
        except Exception:
            jl = None
        record("cmd=log 支持 n", code == "200" and jl and len(jl.get("lines", [])) <= 5,
               body.strip()[:160])

        code, _, body = http_req("token=%s&cmd=status" % TOKEN, post=True)
        record("POST 表单（token/cmd 在请求体）同样可用", code == "200" and "balance" in body,
               "HTTP %s %s" % (code, body.strip()[:120]))

        # check 会真的查一次，并（按当前状态）触发一条推送
        run("uci set powerfee.main.threshold=9999; uci commit powerfee")
        hook_clear()
        code, _, body = http_req("token=%s&cmd=check" % TOKEN, timeout=120)
        recs = hook_records()
        try:
            jc = json.loads(body)
        except Exception:
            jc = None
        record("cmd=check 真的查一次且触发告警推送",
               code == "200" and jc and jc.get("ok") is True and len(recs) == 1,
               "HTTP %s body=%s 推送=%d 条" % (code, body.strip()[:140], len(recs)))
        run("uci set powerfee.main.threshold=20; uci commit powerfee")

        # ---------------- 6. 协调者补丁验证 ----------------
        section("7) 顺带验证：rooms_path 取不到数组时输出带 error 的合法 JSON")
        run("uci set 'powerfee.api.rooms_path=@.nope[*]'; uci commit powerfee")
        rc1, o1, _ = run("powerfee json rooms > /tmp/pf_r1.json; echo rc=$?")
        rc2, o2, _ = run("powerfee json groups > /tmp/pf_r2.json; echo rc=$?")
        _, t1, _ = run("cat /tmp/pf_r1.json")
        _, t2, _ = run("cat /tmp/pf_r2.json")
        try:
            j1 = json.loads(t1)
        except Exception:
            j1 = None
        try:
            j2 = json.loads(t2)
        except Exception:
            j2 = None
        ok = (j1 is not None and j1.get("rooms") == [] and "rooms_path" in str(j1.get("error", ""))
              and j2 is not None and "rooms_path" in str(j2.get("error", ""))
              and "rc=1" in o1 and "rc=1" in o2)
        record("json rooms/groups 都返回带 error 的合法 JSON（rc=1）", ok,
               (t1.strip()[:140] + " || " + t2.strip()[:140]))
        run("uci set 'powerfee.api.rooms_path=@.obj[*]'; uci commit powerfee")

    finally:
        # ---------------- 7. 收尾 ----------------
        section("8) 收尾：恢复配置与状态")
        try:
            run("cp -f %s /etc/config/powerfee && chmod 600 /etc/config/powerfee" % CONF_BAK)
            run("uci -q get powerfee.notify >/dev/null 2>&1 || uci set powerfee.notify=notify; "
                "uci set powerfee.notify.enabled=0; uci set powerfee.notify.http_enabled=0; "
                "uci commit powerfee")
            run("rm -rf /etc/powerfee && mkdir -p /etc/powerfee && chmod 700 /etc/powerfee && "
                "cp -a %s/. /etc/powerfee/ 2>/dev/null; "
                "rm -f /etc/powerfee/state /etc/powerfee/history.csv /etc/powerfee/powerfee.log "
                "/etc/powerfee/powerfee.log.old" % STATE_BAK)
            run("ps w | grep -E 'fake_webhook|fake_smtp' | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null")
            run("rm -rf %s /tmp/fs /tmp/pf_hdr.txt /tmp/pf_body.txt /tmp/pf_r1.json /tmp/pf_r2.json /tmp/pf_unit*.sh" % HOOK_DIR)
            run("/etc/init.d/powerfee restart; sleep 3")
            _, st, _ = run("/etc/init.d/powerfee status")
            _, cfg, _ = run("uci show powerfee | grep -E 'notify\\.|mail\\.enabled'")
            _, notif, _ = run("powerfee status | grep -E '通知推送|邮件提醒'")
            _, room, _ = run("echo \"room_num=[$(uci -q get powerfee.main.room_num)]\"")
            print("   服务状态：%s" % st.strip())
            print("   最终配置：\n" + "\n".join("     " + l for l in cfg.strip().splitlines()))
            print("   通道状态：\n" + "\n".join("     " + l for l in notif.strip().splitlines()))
            print("   房间：%s" % room.strip())
            record("收尾恢复完成（notify/mail 关、房间清空、服务 running）",
                   st.strip() == "running"
                   and "notify.enabled='0'" in cfg and "notify.http_enabled='0'" in cfg
                   and "mail.enabled='0'" in cfg and room.strip() == "room_num=[]",
                   "status=%s %s" % (st.strip(), room.strip()))
        except Exception as exc:  # noqa: BLE001
            print("!! 收尾失败：%s" % exc)
            record("收尾恢复", False, str(exc)[:200])
        try:
            SSH["c"].close()
        except Exception:
            pass

    print("\n================ 汇总 ================")
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
