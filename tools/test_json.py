import io
import json
import os
import sys
import time

import paramiko

HOST = os.environ.get("POWERFEE_HOST", "192.168.1.1")
USER = os.environ.get("POWERFEE_USER", "root")
PASSWORD = os.environ.get("POWERFEE_PASS", "")
_here = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("POWERFEE_BASE") or os.path.dirname(_here)  # tools/ 的父目录 = 仓库根
SSH = {"c": None}


def pick_test_room(run, ssh=None):
    """测试用房间：优先环境变量 POWERFEE_TEST_ROOM，否则取接口返回的第一个房间。
    这样仓库里不必写死任何学校的房间号。"""
    room = os.environ.get("POWERFEE_TEST_ROOM", "")
    if room:
        return room
    cmd = "powerfee json rooms '' 1"
    _, out, _ = run(ssh, cmd) if ssh is not None else run(cmd)
    try:
        import json as _json
        return _json.loads(out)["rooms"][0]["room"]
    except Exception:
        return ""


def connect():
    s = paramiko.SSHClient()
    s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    s.connect(HOST, username=USER, password=PASSWORD, timeout=25, allow_agent=False, look_for_keys=False)
    SSH["c"] = s


def run(cmd, timeout=180, retries=4):
    for attempt in range(retries):
        try:
            if SSH["c"] is None:
                connect()
            _, o, e = SSH["c"].exec_command(cmd, timeout=timeout)
            return o.channel.recv_exit_status(), o.read().decode("utf-8", "replace"), e.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            print("   ! 重连：%s" % exc)
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError(cmd)


def upload(local, remote, mode=0o755):
    data = io.open(os.path.join(BASE, local.replace("/", os.sep)), "rb").read()
    tmp = remote + ".new"
    for _ in range(4):
        try:
            run("mkdir -p %s" % os.path.dirname(remote).replace("\\", "/"))
            stdin, stdout, _ = SSH["c"].exec_command("cat > %s && chmod %o %s && mv -f %s %s" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("rc")
            return
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传重试：%s" % exc)
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError(remote)


# ============================================================================
# 1.1.0 新功能：查询时段（active_hours）与多房间（config room）
#
# 这一段全部在路由器的 /tmp/pf_wip 里跑：
#   * 脚本传到 /tmp/pf_wip/bin/powerfee（不覆盖 /usr/bin/powerfee）
#   * /tmp/pf_wip/bin/uci 是个包装：uci -c /tmp/pf_wip/etc/config，配合 UCI_CONFIG_DIR，
#     把脚本里所有 uci 读写都限制在测试配置上
#   * 状态目录/日志走 main.state_dir、main.log_file，指向 /tmp/pf_wip
#   * 假学校接口（127.0.0.1:8899）、假 webhook（8766）、假 SMTP（2525）都在路由器本机
# 生产 /usr/bin/powerfee、/etc/config/powerfee、/etc/powerfee 全程不动（结尾有断言）。
# ============================================================================

WIP = "/tmp/pf_wip"
WIP_ENV = ("PATH=%s/bin:/usr/sbin:/usr/bin:/sbin:/bin UCI_CONFIG_DIR=%s/etc/config "
           "PF_USAGE_PY=%s/lib/usage.py PF_CHART_PY=%s/lib/chart.py") % (WIP, WIP, WIP, WIP)
WIP_PF = WIP + "/bin/powerfee"
# 「改动前」的主程序副本：必须在 connect() 之后、上传新版**之前**留（见 main 顶部），
# 否则拿到的就是被测脚本自己，对照组会退化成自比自。
OLD_PF = "/tmp/pf_wip_old/powerfee"
WIP_RESULTS = []

FAKE_API_PY = r"""#!/usr/bin/env python3
import json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
STATE = "/tmp/pf_wip/api_state.json"; HITS = "/tmp/pf_wip/api_hits.log"
NAMES = {"1001": ("1\u53f7\u697c", "A101", "\u4e1c\u533a"),
         "2002": ("2\u53f7\u697c", "B202", "\u897f\u533a"),
         "3003": ("3\u53f7\u697c", "C303", "\u4e1c\u533a")}
def rooms():
    try:
        bal = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        bal = {}
    return [{"roomNum": n, "room": r, "building": b, "schoolArea": c,
             "powerBalance": str(bal.get(n, "0.00"))} for n, (b, r, c) in sorted(NAMES.items())]
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        open(HITS, "a", encoding="utf-8").write("%.3f %s %s\n" % (time.time(), self.command, self.path))
        payload = json.dumps({"ret": True, "msg": "", "obj": rooms()}, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    do_GET = _h; do_POST = _h
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", 8899), H).serve_forever()
"""

FAKE_HOOK_PY = r"""#!/usr/bin/env python3
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        os.makedirs("/tmp/pf_wip/hook", exist_ok=True)
        open("/tmp/pf_wip/hook/recv.jsonl", "a", encoding="utf-8").write(
            json.dumps({"path": self.path, "body": body}, ensure_ascii=False) + "\n")
        payload = b'{"ok":true}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    do_GET = _h; do_POST = _h
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", 8766), H).serve_forever()
"""

WIP_CONF_HEAD = """config powerfee 'main'
\toption enabled '1'
\toption room_num '%(room_num)s'
\toption campus ''
\toption building ''
\toption room ''
\toption interval '1800'
\toption retry_interval '300'
\toption threshold '20'
\toption warn_ratio '2'
\toption cooldown '180'
\toption notify_warn '1'
\toption notify_recovery '1'
\toption notify_error '1'
\toption stale_hours '6'
\toption log_file '/tmp/pf_wip/powerfee.log'
\toption log_max_kb '128'
\toption state_dir '/tmp/pf_wip/state'
\toption active_hours '%(active_hours)s'

config api 'api'
\toption url 'http://127.0.0.1:8899/api?from=test'
\toption method 'GET'
\toption timeout '10'
\toption dns_servers ''
\toption rooms_path '@.obj[*]'
\toption ok_path '@.ret'
\toption ok_value 'true'
\toption msg_path '@.msg'
\toption field_id 'roomNum'
\toption field_name 'room'
\toption field_building 'building'
\toption field_campus 'schoolArea'
\toption field_balance 'powerBalance'
\toption unit '\u5ea6'

config mail 'mail'
\toption enabled '%(mail_enabled)s'
\toption transport 'auto'
\toption host '127.0.0.1'
\toption port '2525'
\toption security 'none'
\toption user ''
\toption password ''
\toption from ''
\toption from_name '\u5bbf\u820d\u7535\u91cf\u54e8\u5175'
\toption to 'global@example.com'
\toption tls_verify '0'

config notify 'notify'
\toption enabled '1'
\toption url 'http://127.0.0.1:8766/global'
\toption method 'POST'
\toption content_type 'application/json'
\toption body '{"text":"{text}","room":"{room}","reason":"{reason}","level":"{level}","balance":"{balance}"}'
\toption token ''
\toption token_header 'Authorization'
\toption timeout '5'
\toption http_enabled '0'
\toption http_token ''
"""

WIP_ROOMS = """
config room 'room1'
\toption enabled '1'
\toption num '1001'
\toption mail_to 'a101@example.com'
\toption notify_url 'http://127.0.0.1:8766/roomA'

config room 'room2'
\toption enabled '1'
\toption num '2002'
\toption label '2\u53f7\u697c B202\uff08\u897f\u533a\uff09'
\toption threshold '50'
\toption mail_to 'b202@example.com'
\toption notify_url 'http://127.0.0.1:8766/roomB'
\toption notify_token 'tok-secret-9'

config room 'room3'
\toption enabled '1'
\toption num '3003'
"""

# ---- 1.2.0：命名发件账号 / 推送账号 -------------------------------------------------

# 第二个假 webhook（带请求头记录），用来验证「两个房间各绑不同推送账号，各收到自己那条」
FAKE_HOOK2_PY = r"""#!/usr/bin/env python3
import argparse, json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ARGS = None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        rec = {"method": self.command, "path": self.path,
               "headers": {k: v for k, v in self.headers.items()}, "body": body}
        os.makedirs(ARGS.outdir, exist_ok=True)
        with open(os.path.join(ARGS.outdir, "recv.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        payload = b'{"ok":true}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload))); self.end_headers()
        self.wfile.write(payload)

    do_GET = _h
    do_POST = _h

    def log_message(self, *a):
        pass


ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8767)
ap.add_argument("--outdir", default="/tmp/pf_wip/hook2")
ARGS = ap.parse_args()
ThreadingHTTPServer(("127.0.0.1", ARGS.port), H).serve_forever()
"""

# 账号段（注意：全是编造的值，仓库里不出现任何真实邮箱 / 口令 / 密钥）
WIP_ACCT_SECTIONS = """
config mail_account 'acct_a'
\toption enabled '1'
\toption host '127.0.0.1'
\toption port '2526'
\toption security 'none'
\toption user 'acct_a@example.com'
\toption password 'pw-a-secret'
\toption from 'acct_a@example.com'
\toption from_name '1\u53f7\u697c A101\uff08\u4e1c\u533a\uff09'
\toption tls_verify '0'

config mail_account 'acct_b'
\toption enabled '1'
\toption host '127.0.0.1'
\toption port '2527'
\toption security 'none'
\toption user 'acct_b@example.com'
\toption password 'pw-b-secret'
\toption from 'acct_b@example.com'
\toption from_name '2\u53f7\u697c B202\uff08\u897f\u533a\uff09'
\toption tls_verify '0'

config mail_account 'acct_off'
\toption enabled '0'
\toption host '127.0.0.1'
\toption port '2527'
\toption user 'acct_off@example.com'
\toption password 'pw-off-secret'

config push_account 'sc_a'
\toption enabled '1'
\toption channel 'custom'
\toption url 'http://127.0.0.1:8767/acctA'
\toption method 'POST'
\toption content_type 'application/json'
\toption body '{"text":"{text}","room":"{room}","reason":"{reason}"}'

config push_account 'sc_b'
\toption enabled '1'
\toption channel 'custom'
\toption url 'http://127.0.0.1:8767/acctB'
\toption method 'POST'
\toption content_type 'application/json'
\toption body '{"text":"{text}","room":"{room}","reason":"{reason}"}'
\toption token 'tok-secret-b'
\toption token_header 'X-PF-Token'

config push_account 'sc_unused'
\toption enabled '1'
\toption channel 'serverchan'
\toption url 'https://sctapi.ftqq.com/SCTabcdefghijklmnop.send'
\toption token 'SCTabcdefghijklmnop'
"""

# room1/room2 各绑一个发件账号 + 推送账号；room3 引用两个不存在的账号名（回退 + 标记）；
# room4 用停用的发件账号（回退），推送账号是 sc_a 但房间级 notify_url/notify_token 覆盖它。
WIP_ACCT_ROOMS = """
config room 'room1'
\toption enabled '1'
\toption num '1001'
\toption mail_to 'a101@example.com'
\toption mail_account 'acct_a'
\toption notify_account 'sc_a'

config room 'room2'
\toption enabled '1'
\toption num '2002'
\toption mail_to 'b202@example.com'
\toption mail_account 'acct_b'
\toption notify_account 'sc_b'

config room 'room3'
\toption enabled '1'
\toption num '3003'
\toption mail_to 'c303@example.com'
\toption mail_account 'ghost_mail'
\toption notify_account 'ghost_push'
\toption notify_url 'http://127.0.0.1:8766/roomC'

config room 'room4'
\toption enabled '1'
\toption num '1001'
\toption label '4\u53f7\u697c D404'
\toption mail_to 'd404@example.com'
\toption mail_account 'acct_off'
\toption notify_account 'sc_a'
\toption notify_url 'http://127.0.0.1:8767/roomD'
\toption notify_token 'room-d-token'
"""

# ---- 1.2.0：每日用电量（逐日接口 + 曲线）------------------------------------------

# 假逐日接口（127.0.0.1:8898）：
#   * 历史月最后一天不返回（复刻真实接口的已知坑），当月返回到「昨天」
#   * totalUsed 与 dailyUsed 自洽（从 2026-01-01 起累计），跨月补齐公式算得回来
#   * roomNum=9999 这个房间没有任何逐日数据（测「接口里没数据 -> 退回本地采样」）
FAKE_DAILY_PY = r"""#!/usr/bin/env python3
import argparse, datetime, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HITS = "/tmp/pf_wip/daily_hits.log"
EMPTY_ROOMS = ("9999",)
EPOCH = datetime.date(2026, 1, 1)


def day_used(room, y, m, d):
    return round(8.0 + ((d * 7 + int(room) * 3 + m * 5) % 120) / 10.0, 2)


def total_of(room, upto):
    t = 1000.0
    day = EPOCH
    while day <= upto:
        t += day_used(room, day.year, day.month, day.day)
        day += datetime.timedelta(days=1)
    return round(t, 2)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _h(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = {}
        for kv in (body or query).split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v
        room = params.get("roomNum", "")
        month = params.get("lastDate", "")
        with open(HITS, "a", encoding="utf-8") as fh:
            fh.write("%.3f %s %s\n" % (time.time(), room, month))
        items = []
        if room not in EMPTY_ROOMS and month:
            y, m = [int(x) for x in month.split("-")]
            last = (datetime.date(y + (m == 12), (m % 12) + 1, 1)
                    - datetime.timedelta(days=1)).day
            today = datetime.date.today()
            if (y, m) == (today.year, today.month):
                upto = max(1, today.day - 1)      # 当月：到昨天（1 号时给 1 天，别空）
            else:
                upto = last - 1                   # 历史月：最后一天不返回
            for d in range(1, upto + 1):
                dt = datetime.date(y, m, d)
                items.append({"dailyUsed": "%.2f" % day_used(room, y, m, d),
                              "leftUsed": "615.42", "leftFree": "0.00",
                              "totalUsed": "%.2f" % total_of(room, dt),
                              "dateTime": dt.isoformat()})
        payload = json.dumps({"ret": True, "msg": "查询成功",
                              "obj": {"dailyDetailsInfos": items, "totalPage": 1,
                                      "dailyUsedUnit": "度"}},
                             ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _h
    do_POST = _h

    def log_message(self, *a):
        pass


ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8898)
ARGS = ap.parse_args()
ThreadingHTTPServer(("127.0.0.1", ARGS.port), H).serve_forever()
"""

# room1（编号 1001，接口有逐日数据）+ room9（编号 9999，接口没有 -> 兜底估算）
WIP_DAILY_ROOMS = """
config room 'room1'
\toption enabled '1'
\toption num '1001'
\toption mail_to 'a101@example.com'
\toption notify_url 'http://127.0.0.1:8766/roomA'

config room 'room9'
\toption enabled '1'
\toption num '9999'
\toption label '\u63a2\u5934\u623f\u95f4 R9'
\toption mail_to 'r9@example.com'
\toption notify_url 'http://127.0.0.1:8766/roomR9'
"""


def daily_used_of(room, y, m, d):
    """与假接口同一公式（测试侧独立算期望值）。"""
    return round(8.0 + ((d * 7 + int(room) * 3 + m * 5) % 120) / 10.0, 2)


def prev_month_of(y, m):
    m -= 1
    if m < 1:
        m = 12
        y -= 1
    return y, m


def days_in_month(y, m):
    if m == 12:
        return 31
    import datetime
    return (datetime.date(y + (m == 12), (m % 12) + 1, 1) - datetime.timedelta(days=1)).day


def wip_hook2_records():
    """第二个假 webhook 收到的记录（正文解析成 JSON 放在 rec["json"]）。"""
    _, out, _ = wip_run("cat %s/hook2/recv.jsonl 2>/dev/null" % WIP)
    recs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        try:
            rec["json"] = json.loads(rec.get("body") or "{}")
        except Exception:
            rec["json"] = {}
        recs.append(rec)
    return recs


def wip_mails(subdir):
    """读假 SMTP 收到的邮件：[(信封发件人, 整封 eml 文本)]。

    fake_smtp 把第 n 封写成 00n.eml + 00n.meta（meta 里记着 MAIL FROM 信封发件人）。
    """
    _, out, _ = wip_run("ls %s/%s/*.eml 2>/dev/null | sort" % (WIP, subdir))
    res = []
    for path in out.split():
        _, meta, _ = wip_run("cat %s 2>/dev/null" % path.replace(".eml", ".meta"))
        _, eml, _ = wip_run("cat %s 2>/dev/null" % path)
        sender = ""
        for line in meta.splitlines():
            if line.startswith("mail_from="):
                sender = line.split("=", 1)[1].strip()
        res.append((sender, eml))
    return res


def wip_mail_reset(subdir):
    wip_run("rm -rf %s/%s && mkdir -p %s/%s" % (WIP, subdir, WIP, subdir))


def wip_prod_fingerprint():
    """生产配置指纹：房间编号 + room/mail_account/push_account 段数 + 两个总开关
    + /usr/bin/powerfee 的 md5 + /usr/lib/powerfee/ 的文件清单 + daily_ 选项条数。

    隔离测试全程只读生产配置，跑前跑后指纹必须一模一样（比单看房间数更严：
    命名账号与逐日用量选项也进指纹，免得隔离环境里的改动被 commit 冲进生产）。
    """
    rc, out, _ = wip_run(
        "uci -q get powerfee.main.room_num; "
        "uci -q show powerfee | grep -c '=room$'; "
        "uci -q show powerfee | grep -c '=mail_account$'; "
        "uci -q show powerfee | grep -c '=push_account$'; "
        "uci -q get powerfee.mail.enabled; uci -q get powerfee.notify.enabled; "
        "md5sum /usr/bin/powerfee | cut -d' ' -f1; "
        "ls /usr/lib/powerfee/ | tr '\\n' ','; "
        "uci -q show powerfee | grep -c 'daily_'")
    return "|".join(l.strip() for l in out.strip().splitlines())


def wip_record(name, ok, detail=""):
    WIP_RESULTS.append((name, bool(ok), str(detail)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail else ""))
    sys.stdout.flush()


def wip_run(cmd, timeout=180):
    return run(cmd, timeout=timeout)


def wip_pf(args, timeout=180):
    return wip_run("env %s %s %s" % (WIP_ENV, WIP_PF, args), timeout=timeout)


def wip_upload_abs(local_abs, remote, mode=0o644):
    """把本地文件传到远端（与 upload() 同套路，但不拼 BASE，方便传临时文件）。"""
    data = io.open(local_abs, "rb").read()
    tmp = remote + ".new"
    for _ in range(4):
        try:
            wip_run("mkdir -p %s" % os.path.dirname(remote).replace("\\", "/"))
            stdin, stdout, _ = SSH["c"].exec_command(
                "cat > %s && chmod %o %s && mv -f %s %s" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("rc")
            return
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传重试：%s" % exc)
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError(remote)


def wip_put_text(text, remote, mode=0o644):
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "pf_wip_upload.tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    wip_upload_abs(tmp, remote, mode)


def wip_write_conf(room_num, active_hours="", mail_enabled="0", rooms=""):
    wip_put_text(WIP_CONF_HEAD % {"room_num": room_num, "active_hours": active_hours,
                                  "mail_enabled": mail_enabled} + rooms,
                 WIP + "/etc/config/powerfee", 0o600)


def wip_balances(d):
    wip_put_text(json.dumps(d), WIP + "/api_state.json")


def wip_uci(kvs):
    """在隔离配置里批量 uci set + commit（值用单引号包住，URL 里的 & 不会被 shell 吃掉）。"""
    cmds = ["env %s uci -q set '%s'" % (WIP_ENV, kv) for kv in kvs]
    cmds.append("env %s uci -q commit powerfee" % WIP_ENV)
    return wip_run("; ".join(cmds))


def wip_daily_hits(room=None, month=None):
    """假逐日接口收到的请求数（可按房间/月份过滤）。"""
    cmd = "cat %s/daily_hits.log 2>/dev/null" % WIP
    if room is not None and month is not None:
        cmd += " | grep -c ' %s %s$'" % (room, month)
    elif room is not None:
        cmd += " | grep -c ' %s '" % room
    else:
        cmd += " | wc -l"
    _, out, _ = wip_run(cmd)
    return out.strip() or "0"


def eml_parts(eml):
    """拆一封我们生成的 eml：[(content_type, 解码后的正文), ...]（非 base64 段跳过）。"""
    import base64
    out = []
    for part in eml.split("--"):
        if "Content-Type:" not in part or "base64" not in part:
            continue
        head, _, body = part.partition("\r\n\r\n")
        ctype = ""
        for line in head.splitlines():
            if line.lower().startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        try:
            data = base64.b64decode("".join(body.split())).decode("utf-8", "replace")
        except Exception:
            data = ""
        out.append((ctype, data))
    return out


def eml_subject(eml):
    import base64
    import re as _re
    m = _re.search(r"^Subject: =\?UTF-8\?B\?([^?]+)\?=", eml, _re.M)
    if not m:
        return ""
    return base64.b64decode(m.group(1)).decode("utf-8", "replace")


def eml_part(eml, prefix):
    for ctype, data in eml_parts(eml):
        if ctype.startswith(prefix):
            return data
    return ""


def wip_hook_bodies():
    _, out, _ = wip_run("cat %s/hook/recv.jsonl 2>/dev/null" % WIP)
    res = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            res.append((rec.get("path", ""), json.loads(rec.get("body") or "{}")))
        except Exception:
            pass
    return res


def wip_state(path):
    _, out, _ = wip_run("cat %s 2>/dev/null" % path)
    vals = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v
    return vals


def wip_hits():
    _, out, _ = wip_run("wc -l < %s/api_hits.log 2>/dev/null | tr -d ' '" % WIP)
    return out.strip() or "0"


def wip_reset():
    wip_run("rm -rf %s/state %s/hook %s/mail && mkdir -p %s/state %s/hook %s/mail && "
            "rm -f %s/api_hits.log %s/powerfee.log" % (WIP, WIP, WIP, WIP, WIP, WIP, WIP, WIP))


def wip_cleanup():
    wip_run("ps w | grep -E 'pf_wip_api|pf_wip_hook|pf_wip_daily|fake_smtp' | grep -v grep | "
            "awk '{print $1}' | xargs -r kill 2>/dev/null")
    # uci 的暂存区是全局的 /tmp/.uci：隔离环境里万一留下未提交的 set，
    # 之后任何一次（生产侧的）commit 都会把它冲进 /etc/config。跑完必须清掉。
    wip_run("rm -f /tmp/.uci/powerfee")
    wip_run("rm -rf %s" % WIP)


def wip_daemon(seconds):
    """在隔离环境里跑 N 秒守护进程，返回 (hits, logtail)。"""
    rc, pid, _ = wip_run("env %s sh -c '%s daemon >%s/daemon.log 2>&1 & echo $!'" % (WIP_ENV, WIP_PF, WIP))
    pid = pid.strip().splitlines()[-1]
    time.sleep(seconds)
    hits = wip_hits()
    wip_run("kill %s 2>/dev/null" % pid)
    _, dlog, _ = wip_run("tail -5 %s/powerfee.log 2>/dev/null" % WIP)
    return hits, dlog


# ---- 1.2.0：批量添加房间（room-add 多目标）----------------------------------------
#
# 与上面各段同一套隔离：脚本 /tmp/pf_wip/bin/powerfee、配置 /tmp/pf_wip/etc/config、
# 假接口 127.0.0.1:8899（用 api_hits.log 数请求次数 —— 证明「一次拉取匹配多个房间」）。
# 另有一组「改动前的脚本」对照：connect() 之后、覆盖生产 /usr/bin/powerfee **之前**留下的
# 只读副本（OLD_PF = /tmp/pf_wip_old/powerfee，见 main 顶部），在同样的隔离配置上跑同一条
# 单房间命令，逐字符比对输出/退出码/写入的段；副本本身已含批量实现（路由器装的已是新版）
# 或没拿到副本时，这一组**跳过并打印说明**。

def wip_batch_tests():
    """1.2.0 批量添加的真机隔离验证。返回本段失败的断言列表。"""
    print("\n-- 1.2.0 批量添加房间（一次拉取 / 去重 / 部分失败汇总 / 老行为对照）")
    n0 = len(WIP_RESULTS)
    prod_before = wip_prod_fingerprint()
    wip_run("rm -f /tmp/.uci/powerfee")
    wip_run("mkdir -p %s/bin %s/etc/config %s/state %s/hook %s/lib" % (WIP, WIP, WIP, WIP, WIP))
    upload("files/usr/bin/powerfee", WIP + "/bin/powerfee")
    wip_put_text("#!/bin/sh\nexec /sbin/uci -c /tmp/pf_wip/etc/config \"$@\"\n", WIP + "/bin/uci", 0o755)
    wip_put_text(FAKE_API_PY, WIP + "/pf_wip_api.py")
    wip_run("ps w | grep -E 'pf_wip_api' | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null; sleep 1")
    wip_run("cd %s && (python3 pf_wip_api.py </dev/null >%s/api.log 2>&1 &)" % (WIP, WIP))
    time.sleep(2)
    _, listening, _ = wip_run("netstat -ltn | grep -c ':8899 '")
    wip_record("批量添加：假接口已在 127.0.0.1:8899 监听", listening.strip() == "1",
               "listening=%s" % listening.strip())

    def uget(key):
        _, v, _ = wip_run("env %s uci -q get powerfee.%s" % (WIP_ENV, key))
        return v.strip()

    def nrooms():
        _, v, _ = wip_run("env %s uci -q show powerfee | grep -c '=room$'" % WIP_ENV)
        return v.strip()

    # ① 批量添加 3 个真实房间：只发生一次接口请求
    wip_reset()
    wip_write_conf("")
    wip_balances({"1001": "71.01", "2002": "60.00", "3003": "8.00"})
    rc, out, err = wip_pf("room-add 1001 2002 3003 --mail-to batch@example.com")
    hits = wip_hits()
    wip_record("批量添加 3 个房间只请求接口一次（一次拉取匹配全部目标）",
               rc == 0 and hits == "1", "rc=%s hits=%s" % (rc, hits))
    wip_record("3 个段都建对：num/campus/building/room 都来自接口",
               uget("room1.num") == "1001" and uget("room1.campus") == "\u4e1c\u533a"
               and uget("room1.building") == "1\u53f7\u697c" and uget("room1.room") == "A101"
               and uget("room2.num") == "2002" and uget("room2.building") == "2\u53f7\u697c"
               and uget("room2.room") == "B202" and uget("room2.campus") == "\u897f\u533a"
               and uget("room3.num") == "3003" and uget("room3.room") == "C303",
               "room1=%s/%s/%s/%s room2=%s room3=%s/%s"
               % (uget("room1.num"), uget("room1.campus"), uget("room1.building"), uget("room1.room"),
                  uget("room2.num"), uget("room3.num"), uget("room3.room")))
    wip_record("选项对所有目标生效（3 个段都写了同一个 --mail-to）",
               uget("room1.mail_to") == "batch@example.com"
               and uget("room2.mail_to") == "batch@example.com"
               and uget("room3.mail_to") == "batch@example.com", "")
    wip_record("逐房间报告 + 汇总行（成功 3 个，退出码 0）",
               "A101" in out and "B202" in out and "C303" in out
               and "\u6c47\u603b\uff1a\u6210\u529f 3 \u4e2a" in out,
               (out.strip().splitlines() or [""])[-1])

    # ② 去重：已经在监控里的房间再传一次 -> 跳过、不新建段
    rc, out, err = wip_pf("room-add 2002 --batch")
    wip_record("已经在监控的编号 -> 报「已在监控」跳过、不新建段（退出码 0）",
               rc == 0 and "\u5df2\u5728\u76d1\u63a7" in out and nrooms() == "3",
               (out + err).strip()[:160])
    rc, out, err = wip_pf("room-add 1001 2002 3003")
    wip_record("三个已监控的房间一起再传一次 -> 全部跳过、段数不变",
               rc == 0 and out.count("\u8df3\u8fc7\uff1a") == 3 and nrooms() == "3",
               out.strip().replace("\n", " | ")[:200])

    # ③ 同一批里重复的目标 -> 第二个也跳过（本次已添加）
    wip_reset()
    wip_write_conf("")
    rc, out, err = wip_pf("room-add 1001 1001")
    wip_record("同一批里重复的编号 -> 只建一个段，第二个按「本次已添加」跳过",
               rc == 0 and nrooms() == "1" and out.count("\u8df3\u8fc7\uff1a") == 1,
               (out + err).strip().replace("\n", " | ")[:160])

    # ④ 部分失败：成功的仍加上、失败的单独报出、退出码非 0
    wip_reset()
    wip_write_conf("")
    wip_run("rm -f %s/api_hits.log" % WIP)
    rc, out, err = wip_pf("room-add 1001 9999 2002")
    hits = wip_hits()
    wip_record("部分失败：成功 2 个仍加上（段数 2）、失败 1 个单独报出、退出码非 0",
               rc != 0 and nrooms() == "2" and "\u5931\u8d25\uff1a9999" in out
               and uget("room1.num") == "1001" and uget("room2.num") == "2002"
               and hits == "1", "rc=%s rooms=%s hits=%s" % (rc, nrooms(), hits))
    wip_record("部分失败时汇总行给出成功/跳过/失败计数",
               "\u6c47\u603b\uff1a\u6210\u529f 2 \u4e2a\uff0c\u8df3\u8fc7 0 \u4e2a\uff0c\u5931\u8d25 1 \u4e2a" in out,
               (out.strip().splitlines() or [""])[-1])

    # ⑤ --id 前缀递增 + 不覆盖已有段
    wip_reset()
    wip_write_conf("")
    rc, out, err = wip_pf("room-add 1001 2002 --id dorm")
    wip_record("--id：第一个目标用指定段名，后续自动递增（dorm / dorm2）",
               rc == 0 and uget("dorm.num") == "1001" and uget("dorm2.num") == "2002",
               "dorm=%s dorm2=%s" % (uget("dorm.num"), uget("dorm2.num")))
    rc, out, err = wip_pf("room-add 3003 --id dorm --batch")
    wip_record("--id 指定的段名已被占用 -> 该房间失败、已有段原样不动（不覆盖）",
               rc != 0 and "\u5df2\u5b58\u5728" in out and uget("dorm.num") == "1001"
               and uget("dorm2.num") == "2002" and nrooms() == "2", (out + err).strip()[:160])

    # ⑥ json room-add 多目标：结构化 results[] 与计数
    wip_reset()
    wip_write_conf("")
    rc, out, err = wip_pf("json room-add --batch 1001 9999 2002")
    try:
        jb = json.loads(out)
    except Exception:
        jb = {}
    res = jb.get("results") or []
    wip_record("json room-add 多目标：results[] 逐房间给出 target/num/ok/skipped/id/message",
               jb.get("ok") is False and jb.get("added") == 2 and jb.get("failed") == 1
               and jb.get("skipped") == 0 and jb.get("total") == 3 and len(res) == 3
               and res[0].get("num") == "1001" and res[0].get("ok") is True
               and res[0].get("id") == "room1" and res[0].get("skipped") is False
               and res[1].get("ok") is False
               and "\u6ca1\u6709\u627e\u5230" in (res[1].get("message") or "")
               and res[2].get("num") == "2002" and res[2].get("ok") is True,
               json.dumps(jb, ensure_ascii=False)[:260])
    rc, out, err = wip_pf("json room-add --batch 1001")
    try:
        jb2 = json.loads(out)
    except Exception:
        jb2 = {}
    r0 = (jb2.get("results") or [{}])[0]
    wip_record("json room-add 重复的编号：ok=true + skipped=true（界面据此提示「已在监控」）",
               jb2.get("ok") is True and jb2.get("added") == 0 and jb2.get("skipped") == 1
               and jb2.get("total") == 1 and r0.get("skipped") is True and r0.get("id") == "room1"
               and r0.get("num") == "1001" and uget("room1.num") == "1001" and nrooms() == "2",
               json.dumps(jb2, ensure_ascii=False)[:200])

    # ⑦ 单房间老行为：与改动前的脚本逐字符比对（输出 / 退出码 / 写入的段）
    #    对照组 = connect() 之后、上传新版之前留的只读副本（OLD_PF，见 main 顶部）。
    #    如果那份副本本身已经带批量实现（路由器上早就装了新版），对照就没有意义 ——
    #    这时**明确跳过**并打印说明，而不是拿新版跟新版比、把结论说成「一致」。
    _, old_state, _ = wip_run("test -s %s && echo yes || echo no" % OLD_PF)
    control = old_state.strip() == "yes"
    if control:
        _, old_many, _ = wip_run("grep -c 'cmd_room_add_many' %s || true" % OLD_PF)
        control = old_many.strip() == "0"
        if not control:
            print("   （对照脚本 %s 本身已含批量实现 —— 说明路由器上装的已是新版，"
                  "跳过「改动前没有批量实现」与逐字符对照组）" % OLD_PF)
    else:
        print("   （没有拿到改动前的脚本副本 %s，跳过逐字符对照组）" % OLD_PF)
    if control:
        wip_record("对照脚本（改动前部署的版本）里没有批量实现（grep cmd_room_add_many = 0）",
                   True, "grep=0")
        cases = ["room-add 2002 --id old1 --label '2\u53f7\u697c B202'",
                 "room-add 3003",
                 "room-add --num 1001 --id old2",
                 "json room-add 1001 --id old3"]
        same = []
        for args in cases:
            outs = []
            for prog in (OLD_PF, WIP_PF):
                wip_reset()
                wip_write_conf("")
                wip_balances({"1001": "71.01", "2002": "60.00", "3003": "8.00"})
                rc, o, e = wip_run("env %s %s %s" % (WIP_ENV, prog, args))
                _, cfg, _ = wip_run("env %s uci -q show powerfee | grep -E '=room$|\\.num=|\\.room=|campus|building|mail_to' | sort"
                                    % WIP_ENV)
                outs.append((rc, o, e, cfg))
            same.append(outs[0] == outs[1])
        wip_record("单房间老行为逐字符不变（输出/退出码/写入的段：新脚本 == 改动前脚本，4 组用例）",
                   all(same), "cases=%s" % same)

    prod_after = wip_prod_fingerprint()
    wip_record("批量添加测试全程未动生产（配置指纹 / 脚本 md5 / /usr/lib 清单一致）",
               prod_after != "" and prod_after == prod_before,
               "before=%s after=%s" % (prod_before, prod_after))
    wip_run("rm -f /tmp/.uci/powerfee")
    wip_cleanup()
    print("   （批量添加测试目录 %s 已清理；生产配置与服务未动）" % WIP)
    return [r for r in WIP_RESULTS[n0:] if not r[1]]


def wip_tests():
    """1.1.0 / 1.2.0 新功能的真机验证（隔离在 /tmp/pf_wip）。返回失败的断言列表。"""
    print("\n== 1.1.0/1.2.0 新功能（查询时段 / 多房间 / 命名账号，全部在 /tmp/pf_wip 里跑）")
    WIP_PROD_BEFORE = wip_prod_fingerprint()
    # 跑之前先清 uci 暂存区：隔离环境与生产共用 /tmp/.uci（详见 wip_cleanup 的注释）
    wip_run("rm -f /tmp/.uci/powerfee")
    wip_run("mkdir -p %s/bin %s/etc/config %s/state %s/hook %s/mail %s/hook2 %s/mail_a %s/mail_b %s/lib"
            % (WIP, WIP, WIP, WIP, WIP, WIP, WIP, WIP, WIP))
    upload("files/usr/bin/powerfee", WIP + "/bin/powerfee")
    # 逐日用量助手与绘图模块也放到隔离目录（脚本里用 PF_USAGE_PY / PF_CHART_PY 指过去，
    # 生产 /usr/lib/powerfee/ 一个文件都不碰）
    upload("files/usr/lib/powerfee/usage.py", WIP + "/lib/usage.py")
    upload("files/usr/lib/powerfee/chart.py", WIP + "/lib/chart.py")
    wip_put_text("#!/bin/sh\nexec /sbin/uci -c /tmp/pf_wip/etc/config \"$@\"\n", WIP + "/bin/uci", 0o755)
    wip_put_text(FAKE_API_PY, WIP + "/pf_wip_api.py")
    wip_put_text(FAKE_HOOK_PY, WIP + "/pf_wip_hook.py")
    wip_put_text(FAKE_HOOK2_PY, WIP + "/pf_wip_hook2.py")
    upload("tools/fake_smtp.py", WIP + "/fake_smtp.py")
    wip_run("ps w | grep -E 'pf_wip_api|pf_wip_hook|fake_smtp' | grep -v grep | "
            "awk '{print $1}' | xargs -r kill 2>/dev/null; sleep 1")
    wip_run("cd %s && (python3 pf_wip_api.py </dev/null >%s/api.log 2>&1 &)" % (WIP, WIP))
    wip_run("cd %s && (python3 pf_wip_hook.py </dev/null >%s/hook.log 2>&1 &)" % (WIP, WIP))
    wip_run("cd %s && (python3 pf_wip_hook2.py --port 8767 --outdir %s/hook2 "
            "</dev/null >%s/hook2.log 2>&1 &)" % (WIP, WIP, WIP))
    wip_run("cd %s && (python3 fake_smtp.py --port 2525 --mode plain --outdir %s/mail "
            "</dev/null >%s/smtp.log 2>&1 &)" % (WIP, WIP, WIP))
    # 2526 / 2527 各自要求不同的账号密码：用错凭据会 535 拒收，
    # 于是「两封信分别落在两个端口」就同时证明了 From 与 SMTP 凭据都不同。
    wip_run("cd %s && (python3 fake_smtp.py --port 2526 --mode plain --outdir %s/mail_a "
            "--user acct_a@example.com --password pw-a-secret --require-auth "
            "</dev/null >%s/smtp_a.log 2>&1 &)" % (WIP, WIP, WIP))
    wip_run("cd %s && (python3 fake_smtp.py --port 2527 --mode plain --outdir %s/mail_b "
            "--user acct_b@example.com --password pw-b-secret --require-auth "
            "</dev/null >%s/smtp_b.log 2>&1 &)" % (WIP, WIP, WIP))
    time.sleep(2)
    _, listening, _ = wip_run("netstat -ltn | grep -cE ':(8899|8766|8767|2525|2526|2527) '")
    wip_record("假接口 / 两个假 webhook / 三个假 SMTP 已监听（都在 127.0.0.1）",
               listening.strip() == "6", "listening=%s" % listening.strip())

    # ---- 自检（含 22 个查询时段用例 + 21 个命名账号用例 + 18 个每日用电量用例
    #      + 24 个批量添加用例）----
    rc, out, err = wip_pf("selftest")
    wip_record("selftest 111 项全过（时段边界/跨天/多段/非法输入 + 账号通道判定 + 月份换算/报告时间"
               " + 批量段名递增/匹配/去重/汇总）",
               rc == 0 and "\u5168\u90e8\u901a\u8fc7\uff08111 \u9879\uff09" in out and "FAIL" not in out,
               (out.strip().splitlines() or [""])[-1])

    # ---- 老配置（无 config room）回归 ----
    wip_reset()
    wip_write_conf("1001")
    wip_balances({"1001": "71.01", "2002": "60.00", "3003": "8.00"})
    rc, out, err = wip_pf("check")
    st = wip_state(WIP + "/state/state")
    _, lsout, _ = wip_run("ls %s/state/" % WIP)
    wip_record("老配置：状态仍写 state、历史仍写 history.csv（没多出别的文件）",
               st.get("last_balance") == "71.01" and "history.csv" in lsout
               and "state.legacy" not in lsout and "state.room1" not in lsout,
               json.dumps(st, ensure_ascii=False)[:160])
    rc, out, err = wip_pf("json status")
    try:
        js = json.loads(out)
    except Exception:
        js = {}
    wip_record("json status：老字段不变，新增 active_hours/in_active_window/room_count/rooms",
               js.get("room_num") == "1001" and js.get("balance") == 71.01
               and js.get("active_hours") == "" and js.get("in_active_window") is True
               and js.get("room_count") == 0 and len(js.get("rooms") or []) == 1
               and (js.get("rooms") or [{}])[0].get("num") == "1001",
               json.dumps({k: js.get(k) for k in ("room_num", "active_hours", "in_active_window", "room_count")},
                          ensure_ascii=False))
    wip_run("rm -f %s/hook/recv.jsonl" % WIP)
    wip_balances({"1001": "8.00", "2002": "60.00", "3003": "8.00"})
    wip_pf("check")
    bodies = wip_hook_bodies()
    wip_record("老配置低电量仍走全局 notify.url（room_num 单房间模式）",
               len(bodies) == 1 and bodies[0][0] == "/global" and bodies[0][1].get("reason") == "ok->low",
               json.dumps(bodies, ensure_ascii=False)[:200])

    # ---- 多房间 ----
    wip_reset()
    wip_write_conf("1001", rooms=WIP_ROOMS)
    wip_balances({"1001": "71.01", "2002": "60.00", "3003": "8.00"})
    rc, out, err = wip_pf("check")
    hits = wip_hits()
    st1 = wip_state(WIP + "/state/state.room1")
    st2 = wip_state(WIP + "/state/state.room2")
    st3 = wip_state(WIP + "/state/state.room3")
    wip_record("三个房间只请求接口一次（合并请求，不是每房间一次）", hits == "1", "hits=%s" % hits)
    wip_record("每房间独立状态与档位（ok / warn / low）",
               st1.get("last_balance") == "71.01" and st1.get("last_level") == "ok"
               and st2.get("last_balance") == "60.00" and st2.get("last_level") == "warn"
               and st3.get("last_balance") == "8.00" and st3.get("last_level") == "low",
               "%s/%s/%s" % (st1.get("last_level"), st2.get("last_level"), st3.get("last_level")))
    bodies = wip_hook_bodies()
    paths = sorted(p for p, _ in bodies)
    bmap = dict((p, b) for p, b in bodies)
    wip_record("提醒只发给该房间自己的通道（room2 -> /roomB、room3 -> /global，room1 不推）",
               paths == ["/global", "/roomB"]
               and (bmap.get("/roomB") or {}).get("reason") == "unknown->warn"
               and (bmap.get("/global") or {}).get("reason") == "unknown->low",
               json.dumps(bodies, ensure_ascii=False)[:250])
    wip_record("推送内容带各自房间名（自定义 label / 接口返回的楼栋+房间）",
               (bmap.get("/roomB") or {}).get("room") == "2\u53f7\u697c B202\uff08\u897f\u533a\uff09"
               and (bmap.get("/global") or {}).get("room") == "3\u53f7\u697c C303",
               json.dumps(bmap.get("/roomB"), ensure_ascii=False)[:160])
    rc, out, err = wip_pf("json room-list")
    try:
        jl = json.loads(out)
    except Exception:
        jl = {}
    recs = jl.get("rooms") or []
    wip_record("json room-list 输出全部房间（阈值/收件人按房间生效、令牌只回是否已设置）",
               jl.get("count") == 3 and recs[1].get("threshold") == 50
               and recs[0].get("mail_to") == "a101@example.com" and recs[2].get("mail_to") == "global@example.com"
               and recs[1].get("notify_token_set") is True and "tok-secret-9" not in out,
               json.dumps([(r.get("id"), r.get("threshold"), r.get("mail_to")) for r in recs], ensure_ascii=False)[:200])
    rc, out, err = wip_pf("status")
    wip_record("status 逐房间列出余额",
               out.count("\u623f\u95f4 #") == 3 and "71.01 \u5ea6" in out and "8.00 \u5ea6" in out,
               out.strip()[:200])
    # 每房间独立收件人（真发信到假 SMTP）
    wip_run("rm -f %s/mail/*.eml" % WIP)
    wip_run("rm -rf %s/state && mkdir -p %s/state" % (WIP, WIP))     # 清状态：三个房间都是首查
    wip_run("env %s uci -q set powerfee.mail.enabled=1; env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV))
    wip_balances({"1001": "1.00", "2002": "1.00", "3003": "1.00"})
    wip_pf("check")
    time.sleep(1)
    _, n_eml, _ = wip_run("ls %s/mail/*.eml 2>/dev/null | wc -l" % WIP)
    _, tos, _ = wip_run("grep -h '^To: ' %s/mail/*.eml 2>/dev/null | sort | tr '\\n' ' '" % WIP)
    wip_record("三个房间各发一封到自己的收件人（互不串台）",
               n_eml.strip() == "3" and "a101@example.com" in tos and "b202@example.com" in tos
               and "global@example.com" in tos, "eml=%s %s" % (n_eml.strip(), tos.strip()[:160]))
    wip_run("env %s uci -q set powerfee.mail.enabled=0; env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV))

    # ---- 查询时段 ----
    wip_reset()
    hh = int(time.strftime("%H"))
    win_out = "%02d:00-%02d:00" % ((hh + 1) % 24, (hh + 2) % 24)
    win_in = "%02d:00-%02d:00" % (hh, (hh + 1) % 24)
    wip_write_conf("1001", active_hours=win_out)
    wip_run("env %s uci -q set powerfee.main.interval=5; env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV))
    rc, out, err = wip_pf("json check")
    try:
        jc = json.loads(out)
    except Exception:
        jc = {}
    hits = wip_hits()
    wip_record("窗口外 check 不发请求，返回 skipped=true / in_active_window=false",
               jc.get("skipped") is True and jc.get("in_active_window") is False and hits in ("0", ""),
               "skipped=%s hits=%s" % (jc.get("skipped"), hits))
    hits, dlog = wip_daemon(16)
    wip_record("守护进程在窗口外活着但零请求（16 秒）", hits in ("0", ""), "hits=%s" % hits)
    wip_record("窗口外日志写明「不在查询时段」", "\u4e0d\u5728\u67e5\u8be2\u65f6\u6bb5" in dlog,
               dlog.strip().replace("\n", " | ")[:160])
    wip_run("env %s uci -q set powerfee.main.active_hours='%s'; env %s uci -q commit powerfee"
            % (WIP_ENV, win_in, WIP_ENV))
    hits, dlog = wip_daemon(14)
    wip_record("回到窗口内守护进程按间隔恢复查询（14 秒内 >= 2 次）", int(hits or 0) >= 2, "hits=%s" % hits)
    wip_run("env %s uci -q set powerfee.main.active_hours='%s'; env %s uci -q commit powerfee"
            % (WIP_ENV, win_out, WIP_ENV))
    wip_run("rm -f %s/api_hits.log" % WIP)
    rc, out, err = wip_pf("check --force")
    wip_record("check --force 在窗口外也强制查一次", wip_hits() == "1", "hits=%s" % wip_hits())
    # 窗口外时长不计入失效判定（对照组：没设时段时照旧告警）
    wip_reset()
    old = int(time.time()) - 10 * 3600
    wip_write_conf("1001", active_hours=win_in)
    wip_run("env %s uci -q set 'powerfee.api.url=http://127.0.0.1:1/nope'; "
            "env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV))
    wip_put_text("last_balance=71.01\nlast_level=ok\nlast_ok_at=%d\nconsec_fail=0\n"
                 "poll_count=3\nstale_alerted=0\n" % old, WIP + "/state/state", 0o600)
    wip_put_text("pause_started_at=%d\n" % old, WIP + "/state/window.state", 0o600)
    rc, out, err = wip_pf("check")
    st = wip_state(WIP + "/state/state")
    bodies = wip_hook_bodies()
    wip_record("窗口结束后第一次失败不误报监控失效（窗口外的 10 小时被扣掉）",
               rc == 1 and not bodies and st.get("stale_alerted") in ("0", None)
               and int(st.get("last_ok_at", "0")) > old + 60,
               "stale_alerted=%s last_ok_at=%s" % (st.get("stale_alerted"), st.get("last_ok_at")))
    wip_reset()
    wip_write_conf("1001", active_hours="")
    wip_run("env %s uci -q set 'powerfee.api.url=http://127.0.0.1:1/nope'; "
            "env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV))
    wip_put_text("last_balance=71.01\nlast_level=ok\nlast_ok_at=%d\nconsec_fail=0\n"
                 "poll_count=3\nstale_alerted=0\n" % old, WIP + "/state/state", 0o600)
    rc, out, err = wip_pf("check")
    bodies = wip_hook_bodies()
    wip_record("对照组：没设查询时段时照旧发 stale 告警（老行为没变）",
               rc == 1 and len(bodies) == 1 and bodies[0][1].get("reason") == "stale",
               json.dumps(bodies, ensure_ascii=False)[:160])

    # 多房间 + 查询时段：每个房间的失效基准都要顺延
    # （否则除第一个房间外，早上第一次查询失败就会误报）
    wip_reset()
    wip_write_conf("1001", active_hours=win_in, rooms=WIP_ROOMS)
    wip_run("env %s uci -q set 'powerfee.api.url=http://127.0.0.1:1/nope'; "
            "env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV))
    for _room in ("room1", "room2", "room3"):
        wip_put_text("last_balance=71.01\nlast_level=ok\nlast_ok_at=%d\nconsec_fail=0\n"
                     "poll_count=3\nstale_alerted=0\n" % old,
                     WIP + "/state/state." + _room, 0o600)
    wip_put_text("pause_started_at=%d\n" % old, WIP + "/state/window.state", 0o600)
    rc, out, err = wip_pf("check")
    _shifted = True
    for _room in ("room1", "room2", "room3"):
        _st = wip_state(WIP + "/state/state." + _room)
        if int(_st.get("last_ok_at", "0")) <= old + 60 or _st.get("stale_alerted") not in ("0", None):
            _shifted = False
    bodies = wip_hook_bodies()
    wip_record("多房间 + 查询时段：三个房间的失效基准都被顺延、谁都没误报",
               rc == 1 and not bodies and _shifted, "bodies=%d" % len(bodies))

    # ---- 管理子命令与老数据迁移 ----
    wip_reset()
    wip_write_conf("1001")
    wip_balances({"1001": "71.01", "2002": "45.00", "3003": "8.00"})
    wip_put_text("last_balance=66.60\nlast_level=ok\nlast_ok_at=%d\npoll_count=9\n"
                 "last_alert_at=1\nlast_alert_reason=recovered\n" % (int(time.time()) - 1800),
                 WIP + "/state/state", 0o600)
    wip_put_text("%d,66.60\n" % (int(time.time()) - 3600), WIP + "/state/history.csv", 0o600)
    rc, out, err = wip_pf("room-add 1001 --mail-to a101@example.com")
    st_mig = wip_state(WIP + "/state/state.room1")
    _, hist, _ = wip_run("cat %s/state/history.room1.csv 2>/dev/null" % WIP)
    wip_record("room-add 成功并提示送达方式", rc == 0 and "a101@example.com" in out, (out + err).strip()[:160])
    wip_record("迁移：老 state / history.csv 继承给第一个房间（余额与提醒历史都在）",
               st_mig.get("last_balance") == "66.60" and st_mig.get("last_alert_reason") == "recovered"
               and "66.60" in hist, json.dumps(st_mig, ensure_ascii=False)[:160])
    rc, out, err = wip_pf("room-add 2002 --id r2 --label '2\u53f7\u697c B202' --threshold 50")
    _, got, _ = wip_run("env %s uci -q get powerfee.r2.threshold" % WIP_ENV)
    wip_record("room-add 支持 --id / --label / --threshold", rc == 0 and got.strip() == "50",
               (out + err).strip()[:120])
    rc, out, err = wip_pf("room-set r2 mail_to x@example.com")
    _, got, _ = wip_run("env %s uci -q get powerfee.r2.mail_to" % WIP_ENV)
    wip_record("room-set 改字段生效", rc == 0 and got.strip() == "x@example.com", (out + err).strip()[:120])
    rc, out, err = wip_pf("room-set r2 notify_url")
    wip_record("room-set 缺参数时报错（不静默改成空值）", rc != 0, (out + err).strip()[:120])
    rc, out, err = wip_pf("json room-remove r2")
    try:
        jr = json.loads(out)
    except Exception:
        jr = {}
    _, lsstate, _ = wip_run("ls %s/state/" % WIP)
    wip_record("json room-remove 删段 + 清状态文件 + 返回合法 JSON",
               jr.get("ok") is True and jr.get("count") == 1 and "state.r2" not in lsstate,
               out.strip()[:160])

    # ========================================================================
    # 1.2.0：多个发件邮箱账号（mail_account）/ 多个推送账号（push_account）
    #
    # 全部在隔离环境里跑：两个房间各绑一个发件账号（两个假 SMTP 各自要求不同的
    # 账号密码，用错凭据会 535 拒收），各绑一个推送账号（第二个假 webhook）。
    # ========================================================================
    print("\n-- 1.2.0 命名账号（多发件账号 / 多推送账号）")
    wip_reset()
    for _d in ("mail", "mail_a", "mail_b"):
        wip_mail_reset(_d)
    wip_run("rm -f %s/hook2/recv.jsonl" % WIP)
    wip_write_conf("1001", mail_enabled="1", rooms=WIP_ACCT_ROOMS + WIP_ACCT_SECTIONS)
    # 默认段（mail / notify）也给它自己的身份，才看得出「回退到默认段」发的是谁
    wip_run("env %s uci -q set powerfee.mail.user='base@example.com'; "
            "env %s uci -q set powerfee.mail.password='base-pw-secret'; "
            "env %s uci -q set 'powerfee.mail.from=base@example.com'; "
            "env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV, WIP_ENV, WIP_ENV))
    wip_balances({"1001": "1.00", "2002": "1.00", "3003": "1.00"})
    rc, out, err = wip_pf("check")
    time.sleep(1)
    ma = wip_mails("mail_a")
    mb = wip_mails("mail_b")
    mbase = wip_mails("mail")
    h2 = wip_hook2_records()
    h1 = wip_hook_bodies()
    h2map = {}
    for _r in h2:
        h2map.setdefault(_r["path"], []).append(_r)
    h1paths = sorted(p for p, _ in h1)

    # ① 两个房间各绑不同发件账号：From 与 SMTP 凭据都不同
    # （信封发件人后面可能跟 "SIZE=..." 参数，所以用前缀判断）
    wip_record("两个房间各用不同发件账号（信封发件人与 From 都不同）",
               len(ma) == 1 and len(mb) == 1
               and ma[0][0].startswith("<acct_a@example.com>") and "<acct_a@example.com>" in ma[0][1]
               and mb[0][0].startswith("<acct_b@example.com>") and "<acct_b@example.com>" in mb[0][1],
               "A=%s B=%s" % ([m[0] for m in ma], [m[0] for m in mb]))
    # 两个假 SMTP 都开了 --require-auth 且各认一套账号密码：用错凭据会 535 拒收，
    # 所以「两封信分别落在两个端口」本身就证明 SMTP 凭据确实换了账号。
    _, sa, _ = wip_run("grep -c '\u6536\u5230\u90ae\u4ef6' %s/smtp_a.log 2>/dev/null" % WIP)
    _, sb, _ = wip_run("grep -c '\u6536\u5230\u90ae\u4ef6' %s/smtp_b.log 2>/dev/null" % WIP)
    _, fa, _ = wip_run("grep -c '\u5931\u8d25' %s/smtp_a.log 2>/dev/null" % WIP)
    _, fb, _ = wip_run("grep -c '\u5931\u8d25' %s/smtp_b.log 2>/dev/null" % WIP)
    wip_record("两个假 SMTP 各自只接受自己那套凭据（认证失败次数 0，各收到 1 封）",
               sa.strip() == "1" and sb.strip() == "1"
               and fa.strip() == "0" and fb.strip() == "0",
               "a=%s/%s b=%s/%s" % (sa.strip(), fa.strip(), sb.strip(), fb.strip()))
    wip_record("收件人仍来自房间 mail_to（账号段只管发件方）",
               len(ma) == 1 and "To: a101@example.com" in ma[0][1]
               and len(mb) == 1 and "To: b202@example.com" in mb[0][1],
               "%s | %s" % ([l for l in ma[0][1].splitlines() if l.startswith("To:")][:1],
                            [l for l in mb[0][1].splitlines() if l.startswith("To:")][:1]))

    # ② 两个房间各绑不同推送账号：两个假 webhook 各收到自己那条（不串台）
    wip_record("两个房间各用不同推送账号（/acctA 与 /acctB 各收到自己那条）",
               sorted(h2map.keys()) == ["/acctA", "/acctB", "/roomD"]
               and h2map["/acctA"][0]["json"].get("room") == "1\u53f7\u697c A101"
               and h2map["/acctB"][0]["json"].get("room") == "2\u53f7\u697c B202",
               "paths=%s rooms=%s" % (sorted(h2map.keys()),
                                      [r["json"].get("room") for r in h2]))

    # ③ 房间级 notify_url / notify_token 覆盖账号的 url / token
    wip_record("房间级 notify_url / notify_token 覆盖推送账号的地址与令牌",
               len(h2map.get("/roomD") or []) == 1
               and (h2map["/roomD"][0]["headers"].get("Authorization") == "room-d-token")
               and h2map["/roomD"][0]["json"].get("room") == "4\u53f7\u697c D404"
               and not [r for r in h2map.get("/acctA", [])
                        if r["json"].get("room") == "4\u53f7\u697c D404"],
               "roomD 头=%s" % (h2map.get("/roomD") or [{}])[0].get("headers", {}).get("Authorization"))

    # ④ 引用不存在的账号名 / 停用的账号：回退到默认段继续发，不静默
    wip_record("引用不存在的账号名仍照常发提醒（回退到默认段）",
               len(mbase) == 2
               and "To: c303@example.com" in mbase[0][1] and "<base@example.com>" in mbase[0][1]
               and "To: d404@example.com" in mbase[1][1]
               and "/roomC" in h1paths,
               "回退邮件=%d 封，hook1=%s" % (len(mbase), h1paths))
    _, lg, _ = wip_run("grep -E '\u4e0d\u5b58\u5728|\u5df2\u505c\u7528' %s/powerfee.log 2>/dev/null" % WIP)
    wip_record("账号问题写进日志（说清是哪个账号、什么问题）",
               "ghost_mail" in lg and "ghost_push" in lg and "acct_off" in lg,
               lg.strip().replace("\n", " | ")[:220])
    rc, out, err = wip_pf("status")
    wip_record("status 标出账号问题（账号告警 + 送达行显示回退后的默认段）",
               "\u8d26\u53f7\u544a\u8b66" in out and "ghost_mail" in out and "ghost_push" in out
               and out.count("\u9ed8\u8ba4\u6bb5") >= 2,
               [l for l in out.splitlines() if "\u8d26\u53f7" in l][:3])

    # ⑤ json：房间对象带账号字段、account_issues 列出问题
    rc, out, err = wip_pf("json status")
    try:
        js = json.loads(out)
    except Exception:
        js = {}
    _rooms = dict((r.get("section"), r) for r in (js.get("rooms") or []))
    _r1 = _rooms.get("room1") or {}
    _r3 = _rooms.get("room3") or {}
    _r4 = _rooms.get("room4") or {}
    _iss = js.get("account_issues") or []
    wip_record("json status：每个房间带「发件账号+收件人+推送账号/通道」与账号问题",
               _r1.get("mail_account") == "acct_a" and _r1.get("mail_account_effective") == "acct_a"
               and _r1.get("mail_sender") == "acct_a@example.com" and _r1.get("mail_to") == "a101@example.com"
               and _r1.get("notify_account") == "sc_a" and _r1.get("notify_channel") == "custom"
               and _r3.get("mail_account") == "ghost_mail" and _r3.get("mail_account_effective") == ""
               and "ghost_mail" in (_r3.get("mail_account_issue") or "")
               and "ghost_push" in (_r3.get("notify_account_issue") or "")
               and _r4.get("mail_account_effective") == ""
               and "\u5df2\u505c\u7528" in (_r4.get("mail_account_issue") or "")
               and _r4.get("notify_account_effective") == "sc_a"
               and len(_iss) == 3,
               "issues=%s" % json.dumps(_iss, ensure_ascii=False)[:220])

    # ⑥ 密钥类字段只回「是否已设置」与掩码，真值不出门
    rc, out_m, err = wip_pf("json mail-accounts")
    rc2, out_p, err2 = wip_pf("json push-accounts")
    try:
        jm = json.loads(out_m)
        jp = json.loads(out_p)
    except Exception:
        jm = jp = {}
    _am = dict((a.get("name"), a) for a in (jm.get("accounts") or []))
    _ap = dict((a.get("name"), a) for a in (jp.get("accounts") or []))
    _leak = [s for s in ("pw-a-secret", "pw-b-secret", "pw-off-secret",
                         "tok-secret-b", "SCTabcdefghijklmnop", "base-pw-secret")
             if s in out_m + out_p]
    wip_record("json mail-accounts / push-accounts 里不出现任何口令或密钥真值",
               not _leak and jm.get("count") == 3 and jp.get("count") == 3,
               "泄漏=%s" % _leak)
    wip_record("mail-accounts：user/password 只回是否已设置 + 掩码，字段继承可辨认",
               _am.get("acct_a", {}).get("raw", {}).get("user_set") is True
               and _am.get("acct_a", {}).get("effective", {}).get("user_set") is True
               and _am.get("acct_a", {}).get("effective", {}).get("password_set") is True
               and _am.get("acct_a", {}).get("effective", {}).get("user_hint") == "\u2026\uff08\u5df2\u9690\u85cf\uff09"
               and _am.get("acct_a", {}).get("effective", {}).get("sender") == "acct_a@example.com"
               and _am.get("acct_a", {}).get("inherit") == ["transport"]
               and _am.get("acct_off", {}).get("enabled") is False
               and _am.get("acct_off", {}).get("usable") is False
               and "\u5df2\u505c\u7528" in (_am.get("acct_off", {}).get("issue") or "")
               and _am.get("acct_a", {}).get("used_by") == ["room1"],
               json.dumps(_am.get("acct_a"), ensure_ascii=False)[:260])
    wip_record("push-accounts：token 只回是否已设置，Server酱 地址只回掩码",
               _ap.get("sc_b", {}).get("raw", {}).get("token_set") is True
               and _ap.get("sc_unused", {}).get("effective", {}).get("channel") == "serverchan"
               and "SCTa\u2026" in (_ap.get("sc_unused", {}).get("effective", {}).get("url_masked") or "")
               and _ap.get("sc_a", {}).get("used_by") == ["room1", "room4"],
               json.dumps(_ap.get("sc_unused"), ensure_ascii=False)[:260])

    # ⑦ 账号管理子命令：list / test / room-set 绑定
    rc_l, out_l, err_l = wip_pf("mail-account list")
    rc_p, out_p2, err_p = wip_pf("push-account list")
    wip_record("mail-account list / push-account list 可用且不吐密钥",
               rc_l == 0 and rc_p == 0 and "acct_a" in out_l and "sc_b" in out_p2
               and "pw-a-secret" not in out_l and "tok-secret-b" not in out_p2,
               out_l.strip().splitlines()[0][:80] + " | " + out_p2.strip().splitlines()[0][:80])
    rc, out, err = wip_pf("room-set room1 mail_account acct_b")
    _, got_m, _ = wip_run("env %s uci -q get powerfee.room1.mail_account" % WIP_ENV)
    rc2, out2, err2 = wip_pf("json room-set room1 notify_account sc_b")
    _, got_p, _ = wip_run("env %s uci -q get powerfee.room1.notify_account" % WIP_ENV)
    wip_record("room-set 能绑 mail_account / notify_account（json room-set 同样可用）",
               rc == 0 and got_m.strip() == "acct_b" and rc2 == 0 and got_p.strip() == "sc_b",
               "mail_account=%s notify_account=%s" % (got_m.strip(), got_p.strip()))
    rc, out, err = wip_pf("room-set room2 notify_account ghost_xyz")
    wip_record("room-set 绑到不存在的账号时明确警告（不静默）",
               rc == 0 and "\u8b66\u544a" in (out + err) and "ghost_xyz" in (out + err),
               (out + err).strip().replace("\n", " | ")[:150])
    wip_pf("room-set room2 notify_account sc_b")
    # 换了发件账号后真的换邮箱发（清状态重跑一次）
    wip_run("rm -rf %s/state && mkdir -p %s/state" % (WIP, WIP))
    for _d in ("mail_a", "mail_b"):
        wip_mail_reset(_d)
    wip_run("rm -f %s/hook2/recv.jsonl" % WIP)
    wip_pf("check")
    time.sleep(1)
    ma2 = wip_mails("mail_a")
    mb2 = wip_mails("mail_b")
    wip_record("room-set 换账号后：该房间改从新账号的 SMTP 发出（另一个账号不再收到）",
               len(ma2) == 0 and len(mb2) == 2
               and all("<acct_b@example.com>" in m[0] for m in mb2)
               and "To: a101@example.com" in mb2[0][1] + mb2[1][1],
               "A=%d B=%d %s" % (len(ma2), len(mb2), [m[0] for m in mb2]))
    # mail-account test / push-account test（显式指定账号，不经房间绑定）
    wip_mail_reset("mail_a")
    wip_run("rm -f %s/hook2/recv.jsonl" % WIP)
    rc, out, err = wip_pf("mail-account test acct_a room1")
    time.sleep(1)
    ma3 = wip_mails("mail_a")
    wip_record("mail-account test <名> [房间]：用指定账号给该房间收件人发测试信",
               rc == 0 and len(ma3) == 1 and "<acct_a@example.com>" in ma3[0][1]
               and "To: a101@example.com" in ma3[0][1],
               (out + err).strip()[:150])
    rc, out, err = wip_pf("push-account test sc_b room2")
    time.sleep(1)
    h2b = [r for r in wip_hook2_records() if r["path"] == "/acctB"]
    wip_record("push-account test <名> [房间]：用指定账号推一条（带账号自己的令牌头）",
               rc == 0 and len(h2b) == 1 and h2b[0]["headers"].get("X-PF-Token") == "tok-secret-b"
               and h2b[0]["json"].get("reason") == "test",
               (out + err).strip()[:150])
    rc, out, err = wip_pf("mail-account test ghost_mail")
    wip_record("mail-account test 对不存在的账号直接报错（测试不回退）",
               rc != 0 and "ghost_mail" in (out + err) and "\u6ca1\u6709\u627e\u5230" in (out + err),
               (out + err).strip()[:150])
    rc, out, err = wip_pf("mail-account set acct_a user acct_a@example.com")
    wip_record("mail-account set 写字段生效", rc == 0 and "acct_a@example.com" in out,
               (out + err).strip()[:120])

    # ⑧ 向后兼容：账号段存在但没有任何房间引用它 → 行为与 1.1.0 完全一致
    wip_reset()
    for _d in ("mail", "mail_a", "mail_b"):
        wip_mail_reset(_d)
    wip_run("rm -f %s/hook2/recv.jsonl" % WIP)
    wip_write_conf("1001", mail_enabled="1", rooms=WIP_ROOMS + WIP_ACCT_SECTIONS)
    wip_run("env %s uci -q set powerfee.mail.user='base@example.com'; "
            "env %s uci -q set powerfee.mail.password='base-pw-secret'; "
            "env %s uci -q set 'powerfee.mail.from=base@example.com'; "
            "env %s uci -q commit powerfee" % (WIP_ENV, WIP_ENV, WIP_ENV, WIP_ENV))
    wip_balances({"1001": "1.00", "2002": "1.00", "3003": "1.00"})
    wip_pf("check")
    time.sleep(1)
    ma4 = wip_mails("mail_a")
    mb4 = wip_mails("mail_b")
    mbase4 = wip_mails("mail")
    h14 = sorted(p for p, _ in wip_hook_bodies())
    wip_record("回归：有账号段但房间没引用 → 仍走 mail/notify 段（与 1.1.0 一致）",
               len(ma4) == 0 and len(mb4) == 0 and len(mbase4) == 3
               and all("<base@example.com>" in m[0] for m in mbase4)
               and h14 == ["/global", "/roomA", "/roomB"]
               and not wip_hook2_records(),
               "A=%d B=%d base=%d hook1=%s" % (len(ma4), len(mb4), len(mbase4), h14))

    # ⑨ 守护进程：账号解析在常驻循环里同样生效
    wip_reset()
    for _d in ("mail", "mail_a", "mail_b"):
        wip_mail_reset(_d)
    wip_run("rm -f %s/hook2/recv.jsonl %s/api_hits.log" % (WIP, WIP))
    wip_write_conf("1001", mail_enabled="1", rooms=WIP_ACCT_ROOMS + WIP_ACCT_SECTIONS)
    wip_run("env %s uci -q set powerfee.main.interval='3'; env %s uci -q commit powerfee"
            % (WIP_ENV, WIP_ENV))
    wip_balances({"1001": "1.00", "2002": "1.00", "3003": "1.00"})
    hits, dlog = wip_daemon(14)
    h2d = sorted(set(r["path"] for r in wip_hook2_records()))
    mda = wip_mails("mail_a")
    mdb = wip_mails("mail_b")
    wip_record("守护进程按间隔跑，且各房间走自己的账号（/acctA + /acctB 都收到）",
               int(hits or 0) >= 2 and h2d == ["/acctA", "/acctB", "/roomD"]
               and len(mda) == 1 and len(mdb) == 1,
               "hits=%s hook2=%s mail=%d/%d" % (hits, h2d, len(mda), len(mdb)))

    # ========================================================================
    # 1.2.0：每日用电量（逐日接口 / 缓存 / 跨月补齐 / 兜底估算 / 曲线进邮件与推送）
    #
    # 全部隔离在 /tmp/pf_wip：假逐日接口在 127.0.0.1:8898 —— 历史月最后一天不返回
    # （复刻真实接口的已知坑），逐日数据与 totalUsed 自洽，测试侧独立算期望值。
    # ========================================================================
    print("\n-- 1.2.0 每日用电量（逐日接口 / 缓存 / 跨月补齐 / 兜底 / 报告）")
    wip_reset()
    wip_run("rm -f %s/daily_hits.log" % WIP)
    wip_put_text(FAKE_DAILY_PY, WIP + "/pf_wip_daily.py")
    wip_run("ps w | grep pf_wip_daily | grep -v grep | awk '{print $1}' | "
            "xargs -r kill 2>/dev/null; sleep 1")
    wip_run("cd %s && (python3 pf_wip_daily.py --port 8898 </dev/null >%s/daily.log 2>&1 &)"
            % (WIP, WIP))
    time.sleep(2)
    _, dlisten, _ = wip_run("netstat -ltn | grep -c ':8898 '")
    wip_record("假逐日接口已监听（127.0.0.1:8898）", dlisten.strip() == "1",
               "listening=%s" % dlisten.strip())

    import datetime as _dt
    _today = _dt.date.today()
    _py, _pm = prev_month_of(_today.year, _today.month)
    _prev = "%04d-%02d" % (_py, _pm)
    _prev_last = days_in_month(_py, _pm)
    _exp_filled = daily_used_of("1001", _py, _pm, _prev_last)   # 跨月补齐的期望值

    wip_write_conf("1001", rooms=WIP_DAILY_ROOMS)
    wip_uci(["powerfee.api.daily_url=http://127.0.0.1:8898/daily"])
    wip_balances({"1001": "71.01", "9999": "60.00"})

    # ① 当月：文本曲线 + 摘要 + 来源；② json usage 合法且字段齐全
    rc, out, err = wip_pf("usage --days 7")
    wip_record("usage --days 7：文本曲线 + 摘要 + 数据来源（接口）",
               rc == 0 and "\u2588" in out
               and "\u6570\u636e\u6765\u6e90\uff1a\u5b66\u6821\u63a5\u53e3\u9010\u65e5\u6570\u636e" in out
               and "\u5408\u8ba1" in out,
               (out + err).strip().replace("\n", " | ")[:220])
    rc, out, err = wip_pf("json usage --days 7")
    try:
        ju = json.loads(out)
    except Exception:
        ju = {}
    wip_record("json usage 是合法 JSON：7 天 + text 图 + summary，且不带 png_base64",
               ju.get("ok") is True and ju.get("source") == "api"
               and len(ju.get("days") or []) == 7
               and "\u2588" in (ju.get("text") or "")
               and (ju.get("summary") or {}).get("count") == 7 and "png_base64" not in ju,
               json.dumps(ju.get("summary"), ensure_ascii=False)[:200])
    _d7 = (ju.get("days") or [])
    _d7_last = _d7[-1] if _d7 else {}
    _last_day = _dt.date(_today.year, _today.month, max(1, _today.day - 1))
    _exp_last = daily_used_of("1001", _today.year, _today.month, max(1, _today.day - 1))
    wip_record("当月最后一天（昨天）的用量与接口一致",
               bool(_d7) and _d7_last.get("date") == _last_day.isoformat()
               and abs((_d7_last.get("used") or 0) - _exp_last) < 0.001,
               json.dumps(_d7_last, ensure_ascii=False))

    # ③ 历史月 + 跨月补齐 + 缓存
    _hits0 = int(wip_daily_hits())
    rc, out, err = wip_pf("json usage --month %s" % _prev)
    try:
        jm = json.loads(out)
    except Exception:
        jm = {}
    _hits1 = int(wip_daily_hits())
    _md = (jm.get("days") or [])
    _md_last = _md[-1] if _md else {}
    wip_record("历史月：整月天数齐全（含被恢复的最后一天）",
               jm.get("ok") is True and len(_md) == _prev_last
               and _md_last.get("date") == "%s-%02d" % (_prev, _prev_last),
               "days=%d last=%s" % (len(_md), _md_last.get("date")))
    wip_record("跨月补齐：最后一天 = total(下月1日) - total(上月最后返回日) - used(下月1日)",
               abs((_md_last.get("used") or 0) - _exp_filled) < 0.001,
               "used=%s 期望=%s" % (_md_last.get("used"), _exp_filled))
    _, cache_ls, _ = wip_run("ls %s/state/usage.*.json 2>/dev/null" % WIP)
    wip_record("历史月落缓存 usage.<房间段名>.<YYYY-MM>.json",
               ("usage.room1.%s.json" % _prev) in cache_ls, cache_ls.strip().replace("\n", " "))
    wip_record("首次取历史月打了两次接口（本月数据 + 下月首日，用于补齐）",
               _hits1 - _hits0 == 2, "hits=%d->%d" % (_hits0, _hits1))
    rc, out2, err = wip_pf("json usage --month %s" % _prev)
    _hits2 = int(wip_daily_hits())
    wip_record("缓存命中：第二次不再请求接口（0 次新请求）",
               _hits2 == _hits1 and out2 == out, "hits=%d->%d" % (_hits1, _hits2))

    # ④ --date 指定某天
    _dd = 10
    rc, out, err = wip_pf("usage --date %s-%02d" % (_prev, _dd))
    _exp_d = daily_used_of("1001", _py, _pm, _dd)
    wip_record("--date 指定某天：打印那天的用量",
               rc == 0 and ("%.2f" % _exp_d) in out and ("%s-%02d" % (_prev, _dd)) in out,
               (out + err).strip().replace("\n", " | ")[:200])

    # ⑤ --png 写文件（合法 PNG）
    rc, out, err = wip_pf("usage --month %s --png %s/usage.png" % (_prev, WIP))
    _, pnginfo, _ = wip_run(
        "python3 -c \"d=open('%s/usage.png','rb').read(); print(d[:8].hex(), len(d))\" 2>/dev/null" % WIP)
    _praw = [x for x in pnginfo.split() if x]
    wip_record("usage --png：写出的是合法 PNG（魔数 + 体积合理）",
               rc == 0 and _praw and _praw[0] == "89504e470d0a1a0a"
               and int(_praw[-1] or 0) > 1000,
               pnginfo.strip().replace("\n", " ")[:120])

    # ⑥ 接口里没这个房间 -> 退回本地采样估算；充值跳变剔除（不许出现负用量）
    # 造 8 天采样（每 30 分钟一条，每段降 0.25），其中第 7 天中午充值 +100。
    import time as _time
    _now = int(_time.time())
    _t0 = _now - 8 * 86400
    _t0 = _t0 - (_t0 % 1800) + 300
    _samples = []
    _bal = 300.0
    _topup_ts = _t0 + 6 * 86400 + 12 * 3600
    _t = _t0
    while _t <= _now:
        _samples.append("%d,%.2f" % (_t, _bal))
        _t += 1800
        _bal -= 0.25
        if abs(_t - _topup_ts) < 900:
            _bal += 100.0
    wip_put_text("\n".join(_samples) + "\n", WIP + "/state/history.room9.csv", 0o600)
    # room1 也备一份采样：后面「daily_url 留空 -> 本地采样」那条用它
    wip_put_text("\n".join(_samples) + "\n", WIP + "/state/history.room1.csv", 0o600)
    _topup_day = _dt.datetime.fromtimestamp(_topup_ts).date().isoformat()
    rc, out, err = wip_pf("json usage room9 --days 7")
    try:
        je = json.loads(out)
    except Exception:
        je = {}
    _ed = dict((d["date"], d["used"]) for d in (je.get("days") or []))
    wip_record("接口没这个房间的数据 -> 自动退回本地采样估算（图仍能画）",
               je.get("ok") is True and je.get("source") == "estimate"
               and "\u2588" in (je.get("text") or ""),
               (je.get("source_text") or "") + " | "
               + (je.get("text") or "").split("\n")[0][:40])
    wip_record("充值跳变被剔除：那天没有负用量，也没把 100 度算成用量",
               _ed.get(_topup_day) is not None and 0 <= _ed[_topup_day] <= 15
               and all(v is None or v >= 0 for v in _ed.values()),
               "充值日 %s used=%s 全部=%s" % (_topup_day, _ed.get(_topup_day),
                                             json.dumps(_ed, ensure_ascii=False)[:160]))
    _full_day = (_dt.datetime.fromtimestamp(_topup_ts).date()
                 - _dt.timedelta(days=1)).isoformat()
    wip_record("完整一天（无充值）的估算值 = 12.00（48 段 × 0.25）",
               abs((_ed.get(_full_day) or 0) - 12.0) < 0.001, "used=%s" % _ed.get(_full_day))

    # ⑦ daily_url 留空 -> 直接用本地采样（不请求接口）
    wip_uci(["powerfee.api.daily_url="])
    rc, out, err = wip_pf("usage --days 7")
    wip_record("daily_url 留空：不请求接口，直接本地采样估算且图能画",
               rc == 0 and "\u2588" in out
               and "\u6570\u636e\u6765\u6e90\uff1a\u672c\u5730\u91c7\u6837\u4f30\u7b97" in out,
               (out + err).strip().replace("\n", " | ")[:200])

    # ⑧ 定时报告：report_time 到点自己触发（查询时段之外也要发）
    # 查询窗口设成「现在之后」，报告时间设成「现在」—— 报告必须在窗口外发出来。
    wip_reset()
    wip_mail_reset("mail")
    wip_run("rm -f %s/hook/recv.jsonl %s/api_hits.log" % (WIP, WIP))
    wip_write_conf("1001", mail_enabled="1", rooms=WIP_DAILY_ROOMS)
    _nowdt = _dt.datetime.now()
    _hh = _nowdt.strftime("%H:%M")
    _win_out = "%02d:00-%02d:00" % ((_nowdt.hour + 1) % 24, (_nowdt.hour + 2) % 24)
    wip_uci(["powerfee.api.daily_url=http://127.0.0.1:8898/daily",
             "powerfee.main.report_time=%s" % _hh,
             "powerfee.main.report_days=7",
             "powerfee.main.active_hours=%s" % _win_out,
             "powerfee.room9.enabled=0",
             'powerfee.notify.body={"text":"{text}","reason":"{reason}","chart":"{chart}"}'])
    wip_balances({"1001": "71.01", "9999": "60.00"})
    rc, pid, _ = wip_run("env %s sh -c '%s daemon >%s/daemon.log 2>&1 & echo $!'"
                         % (WIP_ENV, WIP_PF, WIP))
    pid = pid.strip().splitlines()[-1]
    time.sleep(30)
    _qhits = wip_hits()
    wip_run("kill %s 2>/dev/null" % pid)
    time.sleep(1)
    _, rstate, _ = wip_run("cat %s/state/report.state 2>/dev/null" % WIP)
    _rms = wip_mails("mail")
    wip_record("report_time 到点自动发报告（查询时段之外、窗口外不查接口）",
               len(_rms) == 1 and _qhits in ("0", "") and "last_report_date=" in rstate,
               "mails=%d api_hits=%s state=%s" % (len(_rms), _qhits,
                                                  rstate.strip().replace("\n", " ")[:80]))
    import base64 as _b64
    import re as _re
    _rsender, _reml = _rms[0] if _rms else ("", "")
    _rsubj = eml_subject(_reml)
    wip_record("报告邮件：主题/收件人正确、multipart/related 且 PNG 用 Content-ID 内联",
               "\u6bcf\u65e5\u7528\u7535\u62a5\u544a" in _rsubj
               and "To: a101@example.com" in _reml
               and "multipart/related" in _reml
               and "Content-Type: image/png" in _reml
               and "Content-ID: <powerfee-chart-" in _reml
               and "Content-Disposition: inline" in _reml,
               "subject=%s" % _rsubj)
    # HTML 部分（base64）解出来必须引用同一个 Content-ID，并且是合法 HTML
    _rh = eml_part(_reml, "text/html")
    _cm = _re.search(r"Content-ID: <(powerfee-chart-[^>]+)>", _reml)
    _cid = _cm.group(1) if _cm else ""
    wip_record("报告邮件 HTML 里用 <img src=\"cid:…\"> 引用了内联 PNG",
               bool(_cid) and ("cid:%s" % _cid) in _rh and "<html" in _rh.lower(),
               "cid=%s html_len=%d" % (_cid, len(_rh)))
    # PNG 部分解出来真的是 PNG（二进制，不能用上面的 utf-8 解码助手）
    _png_ok = False
    for _part in _reml.split("--"):
        if "image/png" in _part:
            _body = _part.partition("\r\n\r\n")[2].strip()
            try:
                _png_ok = _b64.b64decode("".join(_body.split()))[:8] == b"\x89PNG\r\n\x1a\n"
            except Exception:
                _png_ok = False
    wip_record("报告邮件里的内联图是合法 PNG（解码后魔数正确）", _png_ok, "")
    _rb = wip_hook_bodies()
    _rrep = [b for p, b in _rb if b.get("reason") == "report"]
    wip_record("报告推送：reason=report，{chart} 文本曲线进了 JSON 请求体（多行）",
               len(_rrep) == 1 and "\u2588" in (_rrep[0].get("chart") or "")
               and "\n" in (_rrep[0].get("chart") or ""),
               json.dumps((_rrep[0] if _rrep else {}).get("chart", "")[:60], ensure_ascii=False))
    _, dlog2, _ = wip_run("grep -c '\u5b9a\u65f6\u62a5\u544a\u65f6\u95f4\u5230' %s/powerfee.log 2>/dev/null" % WIP)
    wip_record("守护进程日志写明「定时报告时间到」", dlog2.strip() == "1", dlog2.strip())

    # ⑨ 余额告警邮件也带曲线（同一条 multipart/related 结构）
    wip_mail_reset("mail")
    wip_uci(["powerfee.main.active_hours=", "powerfee.main.report_time="])
    wip_balances({"1001": "1.00", "9999": "60.00"})
    wip_pf("check")
    time.sleep(1)
    _ams = wip_mails("mail")
    _aeml = _ams[0][1] if _ams else ""
    _asubj = eml_subject(_aeml)
    _ah = eml_part(_aeml, "text/html")
    wip_record("余额告警邮件也带内联曲线（multipart/related + image/png + cid 引用）",
               len(_ams) == 1 and "\u7535\u8d39\u4e0d\u8db3" in _asubj
               and "multipart/related" in _aeml and "Content-Type: image/png" in _aeml
               and "cid:powerfee-chart-" in _ah,
               "subject=%s" % _asubj)

    # ---- 批量添加房间（1.2.0）：一次拉取 / 去重 / 部分失败汇总 / 老行为对照 ----
    wip_batch_tests()

    # ---- 生产环境完整性 ----
    prod_after = wip_prod_fingerprint()
    wip_record("生产 /etc/config/powerfee 未被测试改动（房间/账号段数、总开关都没变）",
               prod_after != "" and prod_after == WIP_PROD_BEFORE,
               "before=%s after=%s" % (WIP_PROD_BEFORE, prod_after))
    wip_cleanup()
    print("   （测试目录 %s 已清理；生产配置与服务未动）" % WIP)
    return [r for r in WIP_RESULTS if not r[1]]


connect()
# 覆盖 /usr/bin/powerfee **之前**先留一份只读副本：批量添加那一段拿它当「改动前」对照组
# （OLD_PF）。放在这里而不是那段测试里 —— 那段跑到的时候 /usr/bin/powerfee 已经是新版了。
run("mkdir -p /tmp/pf_wip_old && cp -f /usr/bin/powerfee /tmp/pf_wip_old/powerfee && "
    "chmod 755 /tmp/pf_wip_old/powerfee")
upload("files/usr/bin/powerfee", "/usr/bin/powerfee")
print("== sh -n:", run("sh -n /usr/bin/powerfee && echo OK")[1].strip())

# 用真实房间跑一遍（先设一个测试房间，测完清掉）
TEST_ROOM = pick_test_room(run)
print("\n== 测试房间：%s" % TEST_ROOM)
print("\n== set-room:", run("powerfee json set-room %s" % TEST_ROOM)[1].strip()[:300])
print("\n== json status:")
rc, out, err = run("powerfee json status")
print(out.strip()[:1200])
print("   err:", err.strip()[:200])

print("\n== json check:")
print(run("powerfee json check")[1].strip()[:300])

print("\n== json groups:")
rc, out, err = run("powerfee json groups")
print(out.strip()[:600])
print("   err:", err.strip()[:200])

print("\n== json rooms <关键词> (前 300 字符):")
rc, out, err = run("powerfee json rooms %s" % TEST_ROOM)
print(out.strip()[:300])
print("   err:", err.strip()[:200])
print("   bytes:", len(out))

print("\n== json rooms 无关键词（全量，看截断与体积）:")
rc, out, err = run("powerfee json rooms '' 2000")
print(out.strip()[:200])
print("   bytes:", len(out))
print("   tail:", out.strip()[-120:])

print("\n== json selftest:")
print(run("powerfee json selftest")[1].strip()[:200])

print("\n== json 错误路径:")
print(run("powerfee json bogus; echo rc=$?")[1].strip()[:200])
print(run("powerfee json")[1].strip()[:200])

print("\n== 用 python 校验 JSON 合法性")
CHECK = r'''
import json, subprocess
cmds = [["status"], ["groups"], ["rooms", ROOM_KW], ["check"], ["selftest"], ["rooms", "", "50"]]
for c in cmds:
    r = subprocess.run(["powerfee", "json"] + c, capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        print("OK   json %-22s keys=%s" % (" ".join(c), list(d.keys())[:6]))
    except Exception as e:
        print("FAIL json %-22s %s | %r" % (" ".join(c), e, r.stdout[:120]))
'''.replace("ROOM_KW", json.dumps(TEST_ROOM))
import tempfile
_local_check = os.path.join(tempfile.gettempdir(), "pf_jsoncheck.py")
io.open(_local_check, "w", encoding="utf-8", newline="\n").write(CHECK)
upload(_local_check, "/tmp/_jsoncheck.py")
print(run("python3 /tmp/_jsoncheck.py")[1].strip())
run("rm -f /tmp/_jsoncheck.py")

print("\n== 清掉测试房间")
run("uci set powerfee.main.room_num=''; uci set powerfee.main.campus=''; uci set powerfee.main.building=''; "
    "uci set powerfee.main.room=''; uci commit powerfee; rm -f /etc/powerfee/state /etc/powerfee/history.csv")
print(run("powerfee json status")[1].strip()[:400])

_wip_failed = wip_tests()
print("\n== 1.1.0 新功能小结：%d 项，失败 %d 项"
      % (len(WIP_RESULTS), len(_wip_failed)))
for _n, _ok, _d in WIP_RESULTS:
    if not _ok:
        print("   FAIL %s -- %s" % (_n, _d[:200]))
# 「改动前」的脚本副本（OLD_PF）只给对照组用，测完删掉，别留在路由器上
run("rm -rf /tmp/pf_wip_old")
SSH["c"].close()
