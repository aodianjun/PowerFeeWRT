#!/usr/bin/env python3
"""把 PowerFeeWRT 部署到路由器并跑完整测试。

用法：python deploy_test.py [--no-install]
"""
import argparse
import io
import os
import posixpath
import sys
import time

import paramiko

HOST = os.environ.get("POWERFEE_HOST", "192.168.1.1")
USER = os.environ.get("POWERFEE_USER", "root")
PASSWORD = os.environ.get("POWERFEE_PASS", "")
_here = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("POWERFEE_BASE") or os.path.dirname(_here)  # tools/ 的父目录 = 仓库根
REMOTE_STAGE = "/tmp/pf_stage"

FILES = [
    ("files/usr/bin/powerfee", "/usr/bin/powerfee", 0o755),
    ("files/usr/lib/powerfee/mail.py", "/usr/lib/powerfee/mail.py", 0o755),
    ("files/etc/init.d/powerfee", "/etc/init.d/powerfee", 0o755),
    # 注意：不上传 /etc/config/powerfee —— 那会覆盖用户已经配好的接口与邮箱
    ("tools/fake_smtp.py", "/tmp/pf_stage/fake_smtp.py", 0o755),
]

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
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=25,
                allow_agent=False, look_for_keys=False)
    SSH["c"] = ssh
    return ssh


def run(ssh, cmd, timeout=180, quiet=True, retries=4):
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
            if not quiet and (out or err):
                print("   $ %s\n     rc=%s\n%s" % (cmd, rc, out.strip()))
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


def upload(ssh, local, remote, mode):
    data = io.open(os.path.join(BASE, local.replace("/", os.sep)), "rb").read()
    # 写临时文件再 mv：直接覆盖正在运行的脚本会 ETXTBSY（Text file busy）
    tmp = remote + ".new"
    for attempt in range(4):
        try:
            run(ssh, "mkdir -p %s" % posixpath.dirname(remote))
            stdin, stdout, stderr = SSH["c"].exec_command(
                "cat > %s && chmod %o %s && mv -f %s %s" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                raise RuntimeError("rc=%s" % rc)
            return len(data)
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传失败（第 %d 次）：%s" % (attempt + 1, exc))
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("上传失败：%s" % remote)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-install", action="store_true")
    args = ap.parse_args()

    ssh = connect()
    print("== 已连接 %s" % HOST)

    # ---------- 上传 ----------
    if not args.no_install:
        for local, remote, mode in FILES:
            n = upload(ssh, local, remote, mode)
            print("   上传 %-40s -> %-36s (%d 字节)" % (local, remote, n))
        run(ssh, "mkdir -p /etc/powerfee && chmod 700 /etc/powerfee")
        run(ssh, "chmod 600 /etc/config/powerfee")
        record("上传文件", True, "%d 个文件（配置文件不动）" % len(FILES))

    # ---------- T0 语法检查 ----------
    rc, out, err = run(ssh, "sh -n /usr/bin/powerfee && echo SYNTAX_OK")
    record("shell 语法检查 (sh -n)", "SYNTAX_OK" in out, err.strip()[:200])
    rc, out, err = run(ssh, "python3 -m py_compile /usr/lib/powerfee/mail.py && echo PY_OK")
    record("mail.py 语法检查", "PY_OK" in out, err.strip()[:200])

    # ---------- T1 selftest ----------
    rc, out, err = run(ssh, "powerfee selftest")
    ok = rc == 0 and "全部通过" in out
    record("powerfee selftest（判定逻辑自检）", ok, "" if ok else out[-400:])

    # ---------- T2 rooms / find ----------
    TEST_ROOM = pick_test_room(run, ssh)
    record("取到测试房间（POWERFEE_TEST_ROOM 可覆盖）", bool(TEST_ROOM), "TEST_ROOM=%s" % TEST_ROOM)

    rc, out, err = run(ssh, "powerfee rooms %s | head -5" % TEST_ROOM)
    record("powerfee rooms 关键词过滤", rc == 0 and TEST_ROOM in out, out.strip()[:120].replace("\n", " | "))

    rc, out_all, err = run(ssh, "time powerfee rooms > /tmp/pf_all.txt 2>/tmp/pf_time.txt; wc -l < /tmp/pf_all.txt; cat /tmp/pf_time.txt")
    record("powerfee rooms 全量列表", out_all.strip().split()[0].isdigit() and int(out_all.strip().split()[0]) > 0,
           out_all.strip().replace("\n", " | ")[:200])

    rc, out, err = run(ssh, "powerfee rooms ZZZ_NOPE; echo rc=$?")
    record("搜索无结果时给提示且退出码非 0", "没有匹配" in out and "rc=1" in out, out.strip().replace("\n", " | ")[:160])

    # ---------- T3 set-room ----------
    rc, out, err = run(ssh, "powerfee set-room %s" % TEST_ROOM)
    record("powerfee set-room", rc == 0 and "已设置监控房间" in out, out.strip()[:200].replace("\n", " | "))

    # ---------- T4 check（真实接口 + DNS 回退）----------
    run(ssh, "rm -f /etc/powerfee/state /etc/powerfee/history.csv /etc/powerfee/powerfee.log")
    rc, out, err = run(ssh, "powerfee check; echo rc=$?")
    rc, st, _ = run(ssh, "powerfee status")
    rc, lg, _ = run(ssh, "powerfee log 20")
    ok = "rc=0" in out and "查询成功" in lg and "当前余额    ：" in st
    record("powerfee check（真实接口）", ok,
           [l for l in lg.splitlines() if "查询成功" in l or "DNS" in l][-1:][0] if lg.strip() else "无日志")

    rc, out, err = run(ssh, "powerfee log 20 | grep -c 'DNS 解析'")
    try:
        hits = int(out.strip() or 0)
    except ValueError:
        hits = 0
    record("DNS 回退路径被触发（路由器 DNS 解不出学校域名）", hits >= 1, "日志命中 %d 次" % hits)

    rc, out, err = run(ssh, "powerfee status")
    record("powerfee status 输出完整", "当前余额" in out and "上次查询" in out, "")

    # ---------- T5 history ----------
    rc, out, err = run(ssh, "powerfee check >/dev/null 2>&1; powerfee history 5")
    record("powerfee history", rc == 0 and "日均" in out, out.strip().replace("\n", " | ")[:200])

    # ---------- 假 SMTP 服务器 ----------
    print("\n== 启动假 SMTP 服务器")
    run(ssh, "mkdir -p /tmp/fs && cd /tmp/fs && "
             "openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 2 -nodes "
             "-subj '/CN=localhost' >/dev/null 2>&1 && echo CERT_OK")
    run(ssh, "pkill -f fake_smtp.py 2>/dev/null; rm -rf /tmp/fs/ssl /tmp/fs/plain /tmp/fs/starttls /tmp/fs/badauth; "
             "mkdir -p /tmp/fs/ssl /tmp/fs/plain /tmp/fs/starttls /tmp/fs/badauth")
    # 注意：这台 OpenWrt 没有 nohup，用子 shell 后台（实测可活过 SSH 断开）
    run(ssh, "cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2465 --mode ssl "
             "--outdir /tmp/fs/ssl --cert /tmp/fs/cert.pem --key /tmp/fs/key.pem "
             "--user pf@test --password secret123 </dev/null > /tmp/fs/ssl.log 2>&1 &)")
    run(ssh, "cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2525 --mode plain "
             "--outdir /tmp/fs/plain </dev/null > /tmp/fs/plain.log 2>&1 &)")
    run(ssh, "cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2587 --mode starttls "
             "--outdir /tmp/fs/starttls --cert /tmp/fs/cert.pem --key /tmp/fs/key.pem "
             "</dev/null > /tmp/fs/starttls.log 2>&1 &)")
    run(ssh, "cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2599 --mode ssl "
             "--outdir /tmp/fs/badauth --cert /tmp/fs/cert.pem --key /tmp/fs/key.pem "
             "--user pf@test --password RIGHT </dev/null > /tmp/fs/badauth.log 2>&1 &)")
    time.sleep(3)
    rc, out, err = run(ssh, "netstat -ltn | grep -cE ':(2465|2525|2587|2599) '")
    nports = out.strip()
    detail = nports
    if nports != "4":
        detail = "监听 %s 个；日志：%s" % (nports, run(ssh, "cat /tmp/fs/*.log")[1].strip().replace("\n", " | ")[:200])
    record("假 SMTP 服务器已监听 4 个端口", nports == "4", detail)

    def set_mail(host, port, security, user, password, to, enabled=1, verify=0):
        run(ssh, "uci set powerfee.mail.enabled=%s; uci set powerfee.mail.transport=auto; "
                 "uci set powerfee.mail.host='%s'; uci set powerfee.mail.port='%s'; "
                 "uci set powerfee.mail.security='%s'; uci set powerfee.mail.user='%s'; "
                 "uci set powerfee.mail.password='%s'; uci set powerfee.mail.to='%s'; "
                 "uci set powerfee.mail.tls_verify='%s'; uci commit powerfee"
             % (enabled, host, port, security, user, password, to, verify))

    # ---------- T6 邮件：python3 + ssl ----------
    set_mail("127.0.0.1", 2465, "ssl", "pf@test", "secret123", "a@example.com,b@example.com")
    rc, out, err = run(ssh, "powerfee test-mail; echo rc=$?")
    ok = "测试邮件已发送" in out
    record("test-mail（python3 + ssl + AUTH）", ok, out.strip().replace("\n", " | ")[:200])

    # ---------- T7 邮件：plain ----------
    set_mail("127.0.0.1", 2525, "none", "", "", "c@example.com")
    rc, out, err = run(ssh, "powerfee test-mail; echo rc=$?")
    record("test-mail（plain，无认证）", "测试邮件已发送" in out, out.strip().replace("\n", " | ")[:160])

    # ---------- T8 邮件：starttls ----------
    set_mail("127.0.0.1", 2587, "starttls", "", "", "d@example.com")
    rc, out, err = run(ssh, "powerfee test-mail; echo rc=$?")
    record("test-mail（starttls）", "测试邮件已发送" in out, out.strip().replace("\n", " | ")[:160])

    # ---------- T9 认证失败要能报错 ----------
    set_mail("127.0.0.1", 2599, "ssl", "pf@test", "WRONG", "e@example.com")
    rc, out, err = run(ssh, "powerfee test-mail; echo rc=$?")
    logrc, logtxt, _ = run(ssh, "powerfee log 40 | grep -c '邮件发送失败'")
    record("认证失败时明确报错（不静默）", "rc=1" in out and int(logtxt.strip() or 0) >= 1,
           "日志: %s" % run(ssh, "powerfee log 3")[1].strip().replace("\n", " | ")[:200])

    # ---------- T10 真实告警邮件（check --force）----------
    set_mail("127.0.0.1", 2465, "ssl", "pf@test", "secret123", "owner@example.com")
    run(ssh, "rm -rf /tmp/fs/ssl/*; powerfee check --force; echo rc=$?")
    time.sleep(1)
    rc, out, err = run(ssh, "ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")
    record("check --force 发出真实状态邮件", out.strip() == "1", "收到 %s 封" % out.strip())

    # ---------- T11 阈值告警 + 冷却 ----------
    run(ssh, "rm -rf /tmp/fs/ssl/*; rm -f /etc/powerfee/state /etc/powerfee/history.csv")
    run(ssh, "uci set powerfee.main.threshold=9999; uci commit powerfee")
    rc, out, err = run(ssh, "powerfee check; echo rc=$?")
    time.sleep(1)
    n1 = run(ssh, "ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    rc2, out2, err2 = run(ssh, "powerfee check")
    time.sleep(1)
    n2 = run(ssh, "ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    record("低电量首次告警发信 / 冷却期内不重复", n1 == "1" and n2 == "1",
           "第一次后 %s 封，第二次后 %s 封" % (n1, n2))

    # ---------- T12 恢复告警 ----------
    run(ssh, "uci set powerfee.main.threshold=20; uci commit powerfee")
    run(ssh, "powerfee check")
    time.sleep(1)
    n3 = run(ssh, "ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    record("充值恢复后发一次恢复邮件", n3 == "2", "共 %s 封" % n3)

    # ---------- T13 邮件内容解析 ----------
    check_py = r'''
import email, glob, sys
from email.header import decode_header
paths = sorted(glob.glob("/tmp/fs/ssl/*.eml"))
if not paths:
    print("NO_MAIL"); sys.exit(1)
m = email.message_from_bytes(open(paths[-1], "rb").read())
subj = "".join(t.decode(c or "utf-8") if isinstance(t, bytes) else t for t, c in decode_header(m["Subject"]))
print("SUBJECT:", subj)
print("FROM:", m["From"])
print("TO:", m["To"])
print("CT:", m.get_content_type())
parts = {}
for p in m.walk():
    if p.get_content_type() in ("text/plain", "text/html"):
        parts[p.get_content_type()] = p.get_payload(decode=True).decode("utf-8")
print("HAS_TEXT:", "text/plain" in parts, "HAS_HTML:", "text/html" in parts)
if "text/plain" in parts:
    print("---TEXT---")
    print(parts["text/plain"])
'''
    # 落到系统临时目录，别污染仓库
    import tempfile
    upload_local = os.path.join(tempfile.gettempdir(), "pf_parse_eml.py")
    with io.open(upload_local, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(check_py)
    upload(ssh, upload_local, "/tmp/pf_stage/parse_eml.py", 0o755)
    rc, out, err = run(ssh, "python3 /tmp/pf_stage/parse_eml.py")
    ok = ("HAS_TEXT: True" in out and "HAS_HTML: True" in out and "剩余" in out)
    record("邮件 MIME 结构可被标准库解析（主题/纯文本/HTML）", ok, out.strip().replace("\n", " | ")[:400])

    # ---------- T14 状态持久性 ----------
    rc, out, err = run(ssh, "cat /etc/powerfee/state | tr '\\n' ' '; echo; ls -la /etc/powerfee/")
    record("状态文件落盘且权限 600", "last_level=" in out and "rw-------" in out, out.strip()[:200].replace("\n", " | "))

    # ---------- 汇总 ----------
    print("\n================ 汇总 ================")
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(failed)))
    ssh.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
