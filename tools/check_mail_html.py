#!/usr/bin/env python3
"""把路由器上假 SMTP 收到的邮件取回来，检查 HTML 部分的结构与内容。"""
import base64
import email
import io
import os
import re
import sys
from email.header import decode_header
from html.parser import HTMLParser

import paramiko

HOST = os.environ.get("POWERFEE_HOST", "192.168.1.1")
USER = os.environ.get("POWERFEE_USER", "root")
PASSWORD = os.environ.get("POWERFEE_PASS", "")
_here = os.path.dirname(os.path.abspath(__file__))
# 导出目录默认放在「仓库根的同级」pf_probe/ 下（可用环境变量覆盖）
OUT = os.environ.get("POWERFEE_MAIL_DUMP") or os.path.join(
    os.path.dirname(os.path.dirname(_here)), "pf_probe", "mail_dump")


class Balance(HTMLParser):
    VOID = {"br", "img", "meta", "link", "hr", "input"}

    def __init__(self):
        HTMLParser.__init__(self)
        self.stack = []
        self.errors = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append("多余的 </%s>" % tag)
            return
        if self.stack[-1] != tag:
            self.errors.append("标签不匹配：期望 </%s>，实际 </%s>" % (self.stack[-1], tag))
            return
        self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def main():
    s = paramiko.SSHClient()
    s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    s.connect(HOST, username=USER, password=PASSWORD, timeout=25, allow_agent=False, look_for_keys=False)
    # busybox 没有 base64，用 openssl base64（路由器上有）
    _, o, _ = s.exec_command("for f in /tmp/fs/ssl/*.eml; do echo \"=== $f\"; openssl base64 < $f; done", timeout=120)
    blob = o.read().decode("ascii", "replace")
    s.close()

    os.makedirs(OUT, exist_ok=True)
    parts = re.split(r"^=== ", blob, flags=re.M)[1:]
    print("取回 %d 封邮件\n" % len(parts))
    problems = 0
    for chunk in parts:
        lines = chunk.splitlines()
        name = os.path.basename(lines[0].strip())
        b64 = "".join(l.strip() for l in lines[1:] if l.strip() and not l.startswith("base64:"))
        raw = base64.b64decode(b64)
        m = email.message_from_bytes(raw)
        subj = "".join(t.decode(c or "utf-8") if isinstance(t, bytes) else t for t, c in decode_header(m["Subject"]))
        html = text = None
        for p in m.walk():
            if p.get_content_type() == "text/html":
                html = p.get_payload(decode=True).decode("utf-8")
            elif p.get_content_type() == "text/plain":
                text = p.get_payload(decode=True).decode("utf-8")
        io.open(os.path.join(OUT, name.replace(".eml", ".html")), "w", encoding="utf-8").write(html or "")
        io.open(os.path.join(OUT, name.replace(".eml", ".txt")), "w", encoding="utf-8").write(text or "")

        bp = Balance()
        bp.feed(html or "")
        leftover = [x for x in ("${", "$(", "printf", "awk") if x in (html or "")]
        issues = []
        if bp.stack:
            issues.append("未闭合标签：%s" % bp.stack)
        if bp.errors:
            issues.append("；".join(bp.errors[:3]))
        if leftover:
            issues.append("残留 shell 片段：%s" % leftover)
        if "宿舍电量提醒" not in (html or ""):
            issues.append("HTML 里没有正文标题")
        print("%-12s %s" % (name, subj))
        if issues:
            problems += 1
            print("   ✗ " + " | ".join(issues))
        else:
            print("   ✓ HTML 标签配平、无 shell 残留、含房间名（%d 字节）" % len(html))
    print("\n共 %d 封，问题 %d 封；HTML 已导出到 %s" % (len(parts), problems, OUT))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
