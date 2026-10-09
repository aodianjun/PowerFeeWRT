#!/usr/bin/env python3
"""用真 msmtp 跑邮件分支（不再是桩）：验证 powerfee 生成的 msmtprc 被真 msmtp 接受。"""
import io
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
SSH = {"c": None}


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""))
    sys.stdout.flush()


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
            out = o.read().decode("utf-8", "replace")
            err = e.read().decode("utf-8", "replace")
            return o.channel.recv_exit_status(), out, err
        except Exception as exc:  # noqa: BLE001
            print("   ! 连接异常：%s" % exc)
            try:
                SSH["c"].close()
            except Exception:
                pass
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("命令失败：%s" % cmd)


def upload(local, remote, mode=0o755):
    data = io.open(os.path.join(BASE, local.replace("/", os.sep)), "rb").read()
    tmp = remote + ".new"
    for attempt in range(4):
        try:
            run("mkdir -p %s" % os.path.dirname(remote).replace("\\", "/"))
            stdin, stdout, _ = SSH["c"].exec_command(
                "cat > %s && chmod %o %s && mv -f %s %s" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("rc!=0")
            return
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传失败：%s" % exc)
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("上传失败 %s" % remote)


def main():
    connect()
    print("== 已连接 %s" % HOST)

    # 先把当前版本推上去（测的是最新代码）
    upload("files/usr/bin/powerfee", "/usr/bin/powerfee")
    upload("files/usr/lib/powerfee/mail.py", "/usr/lib/powerfee/mail.py")

    # 清掉桩 msmtp（PATH 里的那个），保证走的是真 msmtp
    run("rm -f /tmp/pf_stage/msmtp")
    rc, out, _ = run("command -v msmtp; msmtp --version | head -3")
    record("真 msmtp 就位", "/usr/bin/msmtp" in out and "msmtp version" in out,
           out.strip().replace("\n", " | ")[:200])

    # 假 SMTP 服务器
    rc, out, _ = run("netstat -ltn | grep -cE ':(2465|2525|2587|2599) '")
    if out.strip() != "4":
        run("pkill -f fake_smtp.py; sleep 1; mkdir -p /tmp/fs/ssl /tmp/fs/plain /tmp/fs/starttls /tmp/fs/badauth")
        run("cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2465 --mode ssl --outdir /tmp/fs/ssl "
            "--cert /tmp/fs/cert.pem --key /tmp/fs/key.pem --user pf@test --password secret123 "
            "</dev/null >/tmp/fs/ssl.log 2>&1 &)")
        run("cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2525 --mode plain --outdir /tmp/fs/plain "
            "</dev/null >/tmp/fs/plain.log 2>&1 &)")
        run("cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2587 --mode starttls --outdir /tmp/fs/starttls "
            "--cert /tmp/fs/cert.pem --key /tmp/fs/key.pem </dev/null >/tmp/fs/starttls.log 2>&1 &)")
        run("cd /tmp/fs && (python3 /tmp/pf_stage/fake_smtp.py --port 2599 --mode ssl --outdir /tmp/fs/badauth "
            "--cert /tmp/fs/cert.pem --key /tmp/fs/key.pem --user pf@test --password RIGHT "
            "</dev/null >/tmp/fs/badauth.log 2>&1 &)")
        time.sleep(3)
    rc, out, _ = run("netstat -ltn | grep -cE ':(2465|2525|2587|2599) '")
    record("假 SMTP 就绪", out.strip() == "4", "监听 %s 个" % out.strip())

    def set_mail(port, security, user, password, verify=0):
        run("uci set powerfee.mail.enabled=1; uci set powerfee.mail.transport='msmtp'; "
            "uci set powerfee.mail.host='127.0.0.1'; uci set powerfee.mail.port='%s'; "
            "uci set powerfee.mail.security='%s'; uci set powerfee.mail.user='%s'; "
            "uci set powerfee.mail.password='%s'; uci set powerfee.mail.to='msmtp@example.com'; "
            "uci set powerfee.mail.tls_verify='%s'; uci commit powerfee"
            % (port, security, user, password, verify))

    # 1) ssl + AUTH
    run("rm -rf /tmp/fs/ssl/*")
    set_mail(2465, "ssl", "pf@test", "secret123")
    rc, out, err = run("powerfee test-mail; echo rc=$?")
    time.sleep(1)
    n = run("ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    record("真 msmtp：ssl + AUTH 投递成功", "测试邮件已发送" in out and n == "1",
           "收到 %s 封；%s" % (n, out.strip().replace("\n", " | ")[:120]))

    # 2) plain（无认证）—— 之前正是这里暴露了「无认证仍写 auth on」的 bug
    run("rm -rf /tmp/fs/plain/*")
    set_mail(2525, "none", "", "")
    rc, out, err = run("powerfee test-mail; echo rc=$?")
    time.sleep(1)
    n = run("ls /tmp/fs/plain/*.eml 2>/dev/null | wc -l")[1].strip()
    record("真 msmtp：plain 无认证投递成功", n == "1", "收到 %s 封；%s" % (n, out.strip().replace("\n", " | ")[:120]))

    # 3) starttls
    run("rm -rf /tmp/fs/starttls/*")
    set_mail(2587, "starttls", "", "")
    rc, out, err = run("powerfee test-mail; echo rc=$?")
    time.sleep(1)
    n = run("ls /tmp/fs/starttls/*.eml 2>/dev/null | wc -l")[1].strip()
    record("真 msmtp：starttls 投递成功", n == "1", "收到 %s 封" % n)

    # 4) 认证失败要报错
    run("rm -rf /tmp/fs/badauth/*")
    set_mail(2599, "ssl", "pf@test", "WRONG")
    rc, out, err = run("powerfee test-mail; echo rc=$?")
    rc2, logtxt, _ = run("powerfee log 30 | grep -c '邮件发送失败'")
    record("真 msmtp：认证失败不静默", "rc=1" in out and int(logtxt.strip() or 0) >= 1,
           "日志命中 %s 次" % logtxt.strip())

    # 4b) 开启证书校验时必须真的拒绝自签证书（证明 tls_verify=1 不是摆设）
    run("rm -rf /tmp/fs/ssl/*")
    set_mail(2465, "ssl", "pf@test", "secret123", verify=1)
    rc, out, err = run("powerfee test-mail; echo rc=$?")
    time.sleep(1)
    n = run("ls /tmp/fs/ssl/*.eml 2>/dev/null | wc -l")[1].strip()
    rc2, why, _ = run("powerfee log 3 | grep -o 'certificate verification failed' | head -1")
    record("真 msmtp：tls_verify=1 时拒绝自签证书", n == "0" and "rc=1" in out,
           "未投递（%s 封），报错含「%s」" % (n, why.strip() or "?"))
    set_mail(2465, "ssl", "pf@test", "secret123", verify=0)

    # 5) 邮件内容仍可被标准库解析（走真 msmtp 的 DATA 通道）
    run("rm -rf /tmp/fs/ssl/*; powerfee test-mail >/dev/null 2>&1")
    time.sleep(1)
    if os.path.exists(os.path.join(BASE, "tools", "parse_eml.py")):
        upload("tools/parse_eml.py", "/tmp/pf_stage/parse_eml.py")
        rc, out, err = run("python3 /tmp/pf_stage/parse_eml.py")
        record("真 msmtp 投递的邮件 MIME 结构正常", "HAS_TEXT: True" in out and "HAS_HTML: True" in out,
               out.strip().replace("\n", " | ")[:200])

    print("\n================ 汇总 ================")
    for name, ok, detail in RESULTS:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    failed = [r for r in RESULTS if not r[1]]
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
