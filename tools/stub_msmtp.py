#!/usr/bin/env python3
"""桩 msmtp：只用于测试 powerfee 的 msmtp 分支。

它做三件事：
  1. 校验 -C 指定的 rc 文件权限必须是 600（msmtp 本身就会拒绝更宽松的权限）
  2. 校验 rc 文件里有 host / port / user / password / from / tls 等必需指令
  3. 按 rc 里的参数把 stdin 的邮件投递到 SMTP 服务器（本测试里是假服务器）

真正的 msmtp 行为以 msmtp 官方为准；这里是为了把 powerfee 侧的
「生成配置 → 调用 msmtp -t」这条链路跑通并断言配置内容正确。
"""
import argparse
import base64
import os
import re
import smtplib
import ssl
import sys


def parse_rc(path):
    conf = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-z_]+)\s*(?::)?\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key != "account" or " " not in line.split(":", 1)[0]:
                conf.setdefault(key, val)
    return conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-C", "--file", required=True)
    ap.add_argument("-t", "--read-recipients", action="store_true")
    args = ap.parse_args()

    mode = os.stat(args.file).st_mode & 0o777
    if mode != 0o600:
        print("stub-msmtp: 配置文件权限是 %o，msmtp 会拒绝（要求 600）" % mode, file=sys.stderr)
        return 1

    conf = parse_rc(args.file)
    required = ["host", "port", "from"]
    if conf.get("auth", "off") == "on":
        # 声明了 auth on 却没有账号密码，真 msmtp 也会报错
        required += ["user", "password"]
    missing = [k for k in required if not conf.get(k)]
    if missing:
        print("stub-msmtp: 缺少必需配置 %s（auth=%s）" % (missing, conf.get("auth")), file=sys.stderr)
        return 1

    raw = sys.stdin.buffer.read()
    if not raw.strip():
        print("stub-msmtp: 邮件内容为空", file=sys.stderr)
        return 1

    # 从邮件头里取收件人（对应 -t 语义）
    head = raw.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
    rcpts = []
    for line in head.splitlines():
        if line.lower().startswith("to:"):
            rcpts += [x.strip() for x in line[3:].replace(",", " ").split() if x.strip()]
    if not rcpts:
        print("stub-msmtp: 邮件头里没有收件人", file=sys.stderr)
        return 1

    host, port = conf["host"], int(conf["port"])
    use_tls = conf.get("tls", "off") == "on"
    starttls = conf.get("tls_starttls", "on") == "on"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        if use_tls and not starttls:
            s = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.ehlo()
            if use_tls and starttls:
                s.starttls(context=ctx)
                s.ehlo()
        if conf.get("auth", "off") == "on":
            s.login(conf["user"], conf["password"])
        s.sendmail(conf["from"], rcpts, raw)
        s.quit()
    except Exception as exc:  # noqa: BLE001
        print("stub-msmtp: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    print("stub-msmtp: 已投递 %s（tls=%s starttls=%s auth=%s）"
          % (rcpts, use_tls, starttls, conf.get("auth")), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
