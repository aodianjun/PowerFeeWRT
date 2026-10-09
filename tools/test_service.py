#!/usr/bin/env python3
"""服务级测试：procd 常驻、轮询节奏、监控失效/恢复告警、开机自启。

依赖 deploy_test.py 已跑过（假 SMTP 服务器在 2465/2525 等端口）。
用法：python test_service.py
"""
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

RESULTS = []


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
SSH = {"c": None}


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""))
    sys.stdout.flush()


def connect():
    s = paramiko.SSHClient()
    s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    s.connect(HOST, username=USER, password=PASSWORD, timeout=25,
              allow_agent=False, look_for_keys=False)
    SSH["c"] = s
    return s


def run(cmd, timeout=180, retries=4):
    last = None
    for attempt in range(retries):
        try:
            if SSH["c"] is None:
                connect()
            _, o, e = SSH["c"].exec_command(cmd, timeout=timeout)
            out = o.read().decode("utf-8", "replace")
            err = e.read().decode("utf-8", "replace")
            rc = o.channel.recv_exit_status()
            return rc, out, err
        except Exception as exc:  # noqa: BLE001
            last = exc
            print("   ! 连接异常（第 %d 次）：%s" % (attempt + 1, exc))
            try:
                SSH["c"].close()
            except Exception:
                pass
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("命令失败：%s（%s）" % (cmd, last))


def upload(local, remote, mode=0o755):
    data = io.open(os.path.join(BASE, local.replace("/", os.sep)), "rb").read()
    tmp = remote + ".new"
    for attempt in range(4):
        try:
            run("mkdir -p %s" % os.path.dirname(remote).replace("\\", "/"))
            stdin, stdout, stderr = SSH["c"].exec_command(
                "cat > %s && chmod %o %s && mv -f %s %s" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                raise RuntimeError("rc=%s" % rc)
            return
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传失败（第 %d 次）：%s" % (attempt + 1, exc))
            try:
                SSH["c"].close()
            except Exception:
                pass
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("上传失败 %s" % remote)


def polls_of(text):
    """从 powerfee status 输出里取「累计查询」次数。"""
    import re
    m = re.search(r"累计查询\s*：\s*(\d+)", text)
    return int(m.group(1)) if m else -1


def main():
    connect()
    print("== 已连接 %s" % HOST)

    # 上传修复后的脚本
    upload("files/usr/bin/powerfee", "/usr/bin/powerfee")
    upload("files/etc/init.d/powerfee", "/etc/init.d/powerfee")
    run("chmod 755 /usr/bin/powerfee /etc/init.d/powerfee")

    # ---------- S0 确保假 SMTP 在跑 ----------
    rc, out, _ = run("netstat -ltn | grep -cE ':(2465|2525) '")
    if out.strip() != "2":
        run("pkill -f fake_smtp.py; cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2465 --mode ssl "
            "--outdir /tmp/fs/ssl --cert /tmp/fs/cert.pem --key /tmp/fs/key.pem "
            "--user pf@test --password secret123 </dev/null >/tmp/fs/ssl.log 2>&1 &)")
        time.sleep(2)
    rc, out, _ = run("netstat -ltn | grep -cE ':(2465|2525) '")
    record("假 SMTP 就绪", out.strip() == "2", "监听 %s" % out.strip())

    # ---------- 复位到干净状态，邮箱指向假服务器，间隔 60 秒 ----------
    run("uci set powerfee.mail.enabled=1; uci set powerfee.mail.host='127.0.0.1'; uci set powerfee.mail.port='2465'; "
        "uci set powerfee.mail.security='ssl'; uci set powerfee.mail.user='pf@test'; "
        "uci set powerfee.mail.password='secret123'; uci set powerfee.mail.to='owner@example.com'; "
        "uci set powerfee.mail.tls_verify='0'; uci set powerfee.main.threshold='20'; "
        "uci set powerfee.main.interval='60'; uci set powerfee.main.stale_hours='6'; uci commit powerfee")
    run("rm -f /etc/powerfee/state /etc/powerfee/history.csv /etc/powerfee/powerfee.log; rm -rf /tmp/fs/ssl/*")
    TEST_ROOM = pick_test_room(run)
    run("powerfee set-room %s >/dev/null" % TEST_ROOM)

    # ---------- S1 服务启动 ----------
    rc, out, err = run("/etc/init.d/powerfee start; sleep 4; /etc/init.d/powerfee status")
    record("init.d start / status", out.strip() == "running", out.strip()[:120])
    rc, out, _ = run("ps w | grep -v grep | grep -c 'powerfee daemon'")
    record("daemon 进程存在", out.strip() == "1", "进程数 %s" % out.strip())

    # ---------- S1b 命名账号（1.2.0，只读检查：不动配置）----------
    rc, out, _ = run("powerfee json status")
    try:
        _js = json.loads(out)
    except Exception:
        _js = {}
    _rm = (_js.get("rooms") or [{}])[0]
    record("json status 带命名账号字段（mail_accounts / push_accounts / account_issues）",
           _js.get("version") == "1.2.4"
           and isinstance(_js.get("mail_accounts"), list)
           and isinstance(_js.get("push_accounts"), list)
           and isinstance(_js.get("account_issues"), list)
           and "mail_account" in _rm and "notify_account" in _rm and "notify_channel" in _rm,
           "version=%s mail=%s push=%s" % (_js.get("version"),
                                          len(_js.get("mail_accounts") or []),
                                          len(_js.get("push_accounts") or [])))
    rc, out, _ = run("powerfee mail-account list; echo rc=$?")
    record("mail-account list 可用（没有账号段时给出添加提示）",
           "rc=0" in out, out.strip().replace("\n", " | ")[:160])

    # ---------- S2 轮询节奏（60 秒间隔）----------
    run("sleep 75")
    rc, out, _ = run("powerfee status")
    polls = polls_of(out)
    rc, hist, _ = run("wc -l < /etc/powerfee/history.csv")
    record("常驻轮询（60 秒间隔，75 秒后应 ≥2 次）", polls >= 2,
           "累计查询=%d 历史采样=%s" % (polls, hist.strip()))

    # ---------- S3 改配置不重启即生效 ----------
    # 把间隔从 60 改成 30：等待足够久后，日志里相邻两次成功查询的间隔应≈30 秒
    run("uci set powerfee.main.interval='30'; uci commit powerfee")
    time.sleep(85)
    rc, logtxt, _ = run("powerfee log 200 | grep '查询成功' | tail -4")
    stamps = []
    for line in logtxt.splitlines():
        try:
            stamps.append(time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            pass
    gaps = [round(stamps[i + 1] - stamps[i]) for i in range(len(stamps) - 1)]
    rc, out2, _ = run("powerfee status")
    p2 = polls_of(out2)
    ok = p2 > polls and gaps and 22 <= gaps[-1] <= 45
    record("改 interval 后无需重启即生效（新间隔≈30s）", ok,
           "累计查询 %d -> %d，最近间隔 %s 秒" % (polls, p2, gaps))

    # ---------- S4 监控失效告警 ----------
    run("/etc/init.d/powerfee stop; sleep 2")
    run("rm -rf /tmp/fs/ssl/*")
    # 先存下真实接口地址，测完恢复（不写死任何学校域名）
    _, saved_url, _ = run("uci -q get powerfee.api.url || uci -q get powerfee.main.api_host")
    SAVED_URL = saved_url.strip()
    if SAVED_URL and "://" not in SAVED_URL:
        SAVED_URL = "https://" + SAVED_URL
    run("uci set powerfee.api=api 2>/dev/null; uci set powerfee.api.url='https://not-exist.invalid/'; uci commit powerfee")
    # 把上次成功时间改到 8 小时前，触发「监控失效」
    rc, st, _ = run("sed -i \"s/^last_ok_at=.*/last_ok_at=$(( $(date +%s) - 8*3600 ))/\" /etc/powerfee/state; grep last_ok_at /etc/powerfee/state")
    rc, out, err = run("powerfee check; echo rc=$?")
    time.sleep(1)
    n1 = run("ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    subj = run("python3 -c \"import email,glob;from email.header import decode_header as d;"
               "p=sorted(glob.glob('/tmp/fs/ssl/*.eml'));"
               "print(''.join(t.decode(c or 'utf-8') if isinstance(t,bytes) else t for t,c in d(email.message_from_bytes(open(p[-1],'rb').read())['Subject'])))\" "
               "2>/dev/null")[1].strip() if n1 == "1" else ""
    record("监控失效时发告警邮件", n1 == "1" and "监控异常" in subj, "邮件 %s 封，主题：%s" % (n1, subj))

    # ---------- S5 监控恢复邮件（必须只有一封：曾因 _reason 被覆盖而发两封）----------
    if SAVED_URL:
        run("uci set 'powerfee.api.url=%s'; uci commit powerfee" % SAVED_URL)
    else:
        record("恢复接口地址", False, "没读到原来的 api.url，请手工检查 /etc/config/powerfee")
        run("uci set powerfee.api.url=''; uci commit powerfee")
    rc, out, err = run("powerfee check; echo rc=$?")
    time.sleep(1)
    n2 = run("ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    subj2 = run("python3 -c \"import email,glob;from email.header import decode_header as d;"
                "p=sorted(glob.glob('/tmp/fs/ssl/*.eml'));"
                "print(''.join(t.decode(c or 'utf-8') if isinstance(t,bytes) else t for t,c in d(email.message_from_bytes(open(p[-1],'rb').read())['Subject'])))\" "
                "2>/dev/null")[1].strip() if n2 == "2" else ""
    record("监控恢复后补发恢复邮件（且只发一封）", n2 == "2" and "已恢复" in subj2,
           "邮件 %s 封，主题：%s" % (n2, subj2))

    # ---------- S6 开机自启 ----------
    run("/etc/init.d/powerfee enable")
    rc, out, _ = run("ls -l /etc/rc.d/ | grep powerfee")
    record("开机自启链接存在（enable 后）", "S99powerfee" in out, out.strip()[:200].replace("\n", " | "))

    # ---------- S7 服务重启后状态保留 ----------
    run("powerfee set-room %s >/dev/null; powerfee check >/dev/null 2>&1" % TEST_ROOM)
    bal1 = run("powerfee status | grep 当前余额")[1].strip()
    run("/etc/init.d/powerfee restart; sleep 5")
    bal2 = run("powerfee status | grep 当前余额")[1].strip()
    rc, out, _ = run("/etc/init.d/powerfee status")
    record("重启服务后状态保留且服务在跑", bal1 == bal2 and bal1 != "" and out.strip() == "running",
           "%s / %s / status=%s" % (bal1, bal2, out.strip()))

    # ---------- 收尾：停服务 ----------
    run("/etc/init.d/powerfee stop; sleep 2")
    rc, out, _ = run("/etc/init.d/powerfee status")
    record("init.d stop 生效", "running" not in out, out.strip()[:80])

    print("\n================ 汇总 ================")
    for name, ok, detail in RESULTS:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    failed = [r for r in RESULTS if not r[1]]
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
