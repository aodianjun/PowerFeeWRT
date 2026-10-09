#!/usr/bin/env python3
"""极简 SMTP 服务器，只用于测试 powerfee 的邮件通道。

支持三种连接方式：plain / starttls / ssl；支持 AUTH LOGIN 与 AUTH PLAIN。
收到的每封邮件写成一个 .eml 文件（外加一个 .meta 记录信封信息），便于断言。

用法：
    fake_smtp.py --port 2525 --mode plain --outdir /tmp/fake_smtp
    fake_smtp.py --port 2465 --mode ssl   --outdir /tmp/fake_smtp \
                 --cert cert.pem --key key.pem --user u --pass p
"""

import argparse
import base64
import os
import socket
import ssl
import sys
import threading
import time

LOCK = threading.Lock()
STATE = {"n": 0}


def log(msg):
    sys.stderr.write("[fake_smtp] %s\n" % msg)
    sys.stderr.flush()


class Session(threading.Thread):
    def __init__(self, conn, addr, args, ctx):
        threading.Thread.__init__(self, daemon=True)
        self.conn = conn
        self.addr = addr
        self.args = args
        self.ctx = ctx
        self.buf = b""

    # ---- 读写 ----
    def send(self, line):
        self.conn.sendall((line + "\r\n").encode("utf-8"))

    def readline(self):
        while b"\r\n" not in self.buf:
            chunk = self.conn.recv(4096)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line.decode("utf-8", "replace")

    # ---- 会话 ----
    def run(self):
        try:
            self.handle()
        except Exception as exc:  # noqa: BLE001
            log("会话异常：%s: %s" % (type(exc).__name__, exc))
        finally:
            try:
                self.conn.close()
            except Exception:
                pass

    def handle(self):
        a = self.args
        helo = "unknown"
        mail_from = None
        rcpts = []
        authed = False
        self.send("220 fake-smtp ready")
        while True:
            line = self.readline()
            if line is None:
                return
            up = line.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                helo = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
                self.send("250-fake-smtp greets %s" % helo)
                if a.mode == "starttls" and not isinstance(self.conn, ssl.SSLSocket):
                    self.send("250-STARTTLS")
                self.send("250-AUTH LOGIN PLAIN")
                self.send("250-8BITMIME")
                self.send("250 SIZE 10485760")
            elif up.startswith("STARTTLS"):
                if a.mode != "starttls":
                    self.send("502 not supported")
                    continue
                self.send("220 Ready to start TLS")
                self.conn = self.ctx.wrap_socket(self.conn, server_side=True)
                self.buf = b""
                log("STARTTLS 完成")
            elif up.startswith("AUTH LOGIN"):
                parts = line.split()
                if len(parts) > 2:
                    user = base64.b64decode(parts[2]).decode("utf-8", "replace")
                    self.send("334 " + base64.b64encode(b"Password:").decode())
                else:
                    self.send("334 " + base64.b64encode(b"Username:").decode())
                    user = base64.b64decode(self.readline() or "").decode("utf-8", "replace")
                    self.send("334 " + base64.b64encode(b"Password:").decode())
                password = base64.b64decode(self.readline() or "").decode("utf-8", "replace")
                if a.user and (user != a.user or password != a.password):
                    log("AUTH 失败 user=%r" % user)
                    self.send("535 5.7.8 authentication failed")
                else:
                    authed = True
                    log("AUTH 成功 user=%r" % user)
                    self.send("235 2.7.0 accepted")
            elif up.startswith("AUTH PLAIN"):
                parts = line.split()
                blob = parts[2] if len(parts) > 2 else (self.readline() or "")
                try:
                    _, user, password = base64.b64decode(blob).decode("utf-8").split("\x00")
                except Exception:
                    user = password = ""
                if a.user and (user != a.user or password != a.password):
                    log("AUTH PLAIN 失败 user=%r" % user)
                    self.send("535 5.7.8 authentication failed")
                else:
                    authed = True
                    self.send("235 2.7.0 accepted")
            elif up.startswith("MAIL FROM"):
                mail_from = line.split(":", 1)[1].strip()
                self.send("250 2.1.0 ok")
            elif up.startswith("RCPT TO"):
                rcpts.append(line.split(":", 1)[1].strip())
                self.send("250 2.1.5 ok")
            elif up.startswith("DATA"):
                if a.require_auth and not authed:
                    self.send("530 5.7.0 authentication required")
                    continue
                self.send("354 End data with <CR><LF>.<CR><LF>")
                data = b""
                while True:
                    chunk = self.conn.recv(65536)
                    if not chunk:
                        return
                    data += chunk
                    if b"\r\n.\r\n" in data:
                        break
                body = data.split(b"\r\n.\r\n", 1)[0]
                # 去掉点填充
                body = body.replace(b"\r\n..", b"\r\n.")
                self.store(body, mail_from, rcpts, helo)
                mail_from, rcpts = None, []
                self.send("250 2.0.0 ok: queued")
            elif up.startswith("RSET"):
                mail_from, rcpts = None, []
                self.send("250 2.0.0 ok")
            elif up.startswith("NOOP"):
                self.send("250 2.0.0 ok")
            elif up.startswith("QUIT"):
                self.send("221 2.0.0 bye")
                return
            else:
                self.send("502 5.5.2 command not implemented")

    def store(self, body, mail_from, rcpts, helo):
        a = self.args
        with LOCK:
            STATE["n"] += 1
            n = STATE["n"]
        base = os.path.join(a.outdir, "%03d" % n)
        with open(base + ".eml", "wb") as fh:
            fh.write(body)
        with open(base + ".meta", "w", encoding="utf-8") as fh:
            fh.write("mail_from=%s\n" % mail_from)
            fh.write("rcpt=%s\n" % ",".join(rcpts))
            fh.write("helo=%s\n" % helo)
            fh.write("mode=%s\n" % a.mode)
            fh.write("time=%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        log("收到邮件 #%d：from=%s rcpt=%s bytes=%d" % (n, mail_from, rcpts, len(body)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--mode", default="plain", choices=["plain", "starttls", "ssl"])
    ap.add_argument("--outdir", default="/tmp/fake_smtp")
    ap.add_argument("--cert", default="")
    ap.add_argument("--key", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--require-auth", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    ctx = None
    if args.mode in ("ssl", "starttls"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.cert, args.key)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(16)
    log("监听 %s:%d（mode=%s，outdir=%s）" % (args.bind, args.port, args.mode, args.outdir))

    while True:
        conn, addr = srv.accept()
        if args.mode == "ssl":
            try:
                conn = ctx.wrap_socket(conn, server_side=True)
            except Exception as exc:  # noqa: BLE001
                log("TLS 握手失败：%s" % exc)
                conn.close()
                continue
        Session(conn, addr, args, ctx).start()


if __name__ == "__main__":
    main()
