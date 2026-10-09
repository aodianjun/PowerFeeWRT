#!/usr/bin/env python3
"""收尾：用 install.sh 做一次端到端安装，然后清理测试残留、恢复干净配置。

用法：python finalize.py [--keep-test-config]
"""
import argparse
import hashlib
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
STAGE = "/tmp/pf_stage"
SSH = {"c": None}

UPLOAD_TREE = [
    ("files/usr/bin/powerfee", STAGE + "/files/usr/bin/powerfee", 0o755),
    ("files/usr/lib/powerfee/mail.py", STAGE + "/files/usr/lib/powerfee/mail.py", 0o755),
    ("files/etc/init.d/powerfee", STAGE + "/files/etc/init.d/powerfee", 0o755),
    ("files/etc/config/powerfee", STAGE + "/files/etc/config/powerfee", 0o644),
    # 查询端点：install.sh 会把它拷到 /www/cgi-bin/powerfee，缺了 install.sh 会失败
    ("files/www/cgi-bin/powerfee", STAGE + "/files/www/cgi-bin/powerfee", 0o755),
    ("install.sh", STAGE + "/install.sh", 0o755),
]


def connect():
    s = paramiko.SSHClient()
    s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    s.connect(HOST, username=USER, password=PASSWORD, timeout=25,
              allow_agent=False, look_for_keys=False)
    SSH["c"] = s


def run(cmd, timeout=240, retries=5):
    last = None
    for attempt in range(retries):
        try:
            if SSH["c"] is None:
                connect()
            _, o, e = SSH["c"].exec_command(cmd, timeout=timeout)
            out = o.read().decode("utf-8", "replace")
            err = e.read().decode("utf-8", "replace")
            return o.channel.recv_exit_status(), out, err
        except Exception as exc:  # noqa: BLE001
            last = exc
            print("   ! 连接异常：%s" % exc)
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
            run("mkdir -p %s" % posixpath.dirname(remote))
            stdin, stdout, _ = SSH["c"].exec_command(
                "cat > %s && chmod %o %s && mv -f %s %s" % (tmp, mode, tmp, tmp, remote))
            stdin.write(data)
            stdin.flush()
            stdin.channel.shutdown_write()
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError("rc!=0")
            return hashlib.md5(data).hexdigest()
        except Exception as exc:  # noqa: BLE001
            print("   ! 上传失败：%s" % exc)
            SSH["c"] = None
            time.sleep(3)
    raise RuntimeError("上传失败 %s" % remote)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-test-config", action="store_true")
    args = ap.parse_args()

    connect()
    print("== 已连接 %s\n" % HOST)

    print("== 1) 上传完整项目树到 %s" % STAGE)
    local_md5 = {}
    for local, remote, mode in UPLOAD_TREE:
        local_md5[remote] = upload(local, remote, mode)
        print("   %-45s %s" % (local, local_md5[remote]))

    print("\n== 2) 跑 install.sh（端到端安装）")
    rc, out, err = run("sh %s/install.sh 2>&1" % STAGE)
    print(out.strip()[:1500])
    if err.strip():
        print("stderr:", err.strip()[:300])

    print("\n== 3) 核对安装结果")
    checks = [
        ("/usr/bin/powerfee", local_md5[STAGE + "/files/usr/bin/powerfee"]),
        ("/usr/lib/powerfee/mail.py", local_md5[STAGE + "/files/usr/lib/powerfee/mail.py"]),
        ("/etc/init.d/powerfee", local_md5[STAGE + "/files/etc/init.d/powerfee"]),
    ]
    for path, want in checks:
        rc, got, _ = run("md5sum %s | cut -d' ' -f1" % path)
        print("   %-32s %s %s" % (path, got.strip(), "OK" if got.strip() == want else "不一致！期望 " + want))
    rc, out, _ = run("ls -l /etc/config/powerfee; grep -c '^/etc/powerfee$' /etc/sysupgrade.conf; "
                     "ls /etc/rc.d/ | grep powerfee | tr '\\n' ' '; /etc/init.d/powerfee status")
    print("   " + out.strip().replace("\n", "\n   "))

    if not args.keep_test_config:
        print("\n== 4) 恢复干净配置（关掉邮件、清掉测试房间与状态）")
        run("uci set powerfee.mail.enabled=0; uci set powerfee.mail.host='smtp.qq.com'; "
            "uci set powerfee.mail.port='465'; uci set powerfee.mail.security='ssl'; "
            "uci set powerfee.mail.user=''; uci set powerfee.mail.password=''; "
            "uci set powerfee.mail.from=''; uci set powerfee.mail.to=''; "
            "uci set powerfee.mail.tls_verify='1'; "
            "uci set powerfee.main.room_num=''; uci set powerfee.main.campus=''; "
            "uci set powerfee.main.building=''; uci set powerfee.main.room=''; "
            "uci set powerfee.main.threshold='20'; uci set powerfee.main.warn_ratio='2'; "
            "uci set powerfee.main.interval='1800'; uci set powerfee.main.retry_interval='300'; "
            "uci set powerfee.main.cooldown='180'; uci set powerfee.main.stale_hours='6'; "
            "uci set powerfee.main.notify_warn='1'; uci set powerfee.main.notify_recovery='1'; "
            "uci set powerfee.main.notify_error='1'; "
            # 通知推送与查询端点也关掉（section 不存在时先建，否则 uci set 会报 Invalid argument）
            "uci -q get powerfee.notify >/dev/null 2>&1 || uci set powerfee.notify=notify; "
            "uci set powerfee.notify.enabled='0'; uci set powerfee.notify.http_enabled='0'; "
            "uci commit powerfee")
        run("rm -f /etc/powerfee/state /etc/powerfee/history.csv /etc/powerfee/powerfee.log /etc/powerfee/powerfee.log.old")
        rc, out, _ = run("uci show powerfee | grep -v password")
        print(out.strip())

    print("\n== 5) 清理测试残留")
    # busybox 的 pkill -f 不可靠（实测杀不掉），改成按 PID 杀
    run("ps w | grep -E 'fake_smtp|fake_webhook' | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null; sleep 1; "
        "ps w | grep -c '[f]ake_smtp'")
    run("rm -rf /tmp/fs /tmp/pf_stage /tmp/pf_hook /tmp/pf_all.txt /tmp/pf_time.txt /tmp/pf.json /tmp/pf_rooms.json "
        "/tmp/daemon_out.txt /tmp/daemon.pid /tmp/d_out.txt /tmp/d_err.txt /tmp/d.pid /tmp/powerfee.* "
        "/tmp/msmtp*.apk /tmp/rn.txt /tmp/ids.txt /tmp/a.txt /tmp/b.txt /tmp/c.txt /tmp/pf_stage 2>/dev/null")
    rc, out, _ = run("ls /tmp | grep -Ei 'pf|fs|msmtp|powerfee|daemon' ; echo '(以上应为空)'")
    print(out.strip())
    rc, out, _ = run("ps w | grep -v grep | grep -E 'fake_smtp|cat >' ; echo '(以上应为空)'")
    print(out.strip())

    print("\n== 6) 最终状态")
    run("/etc/init.d/powerfee restart; sleep 4")
    for cmd in ["powerfee selftest | tail -2", "powerfee status", "/etc/init.d/powerfee status",
                "logread -e powerfee | tail -3"]:
        rc, out, err = run(cmd)
        print("   $ %s\n%s" % (cmd, "\n".join("     " + l for l in out.strip().splitlines())))
    print("\n== 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
