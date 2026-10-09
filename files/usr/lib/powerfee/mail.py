#!/usr/bin/env python3
"""powerfee 的邮件投递助手。

从 stdin 读入一整封 RFC822 邮件（由 /usr/bin/powerfee 拼好），通过 SMTP 投递。
只用标准库（smtplib / ssl），不装任何第三方包 —— 与托盘版 / 安卓版"零依赖"的取向一致。

用法（一般不用手敲，由 powerfee 调用）：
    mail.py --host smtp.qq.com --port 465 --security ssl \
            --user you@qq.com --password 授权码 \
            --from you@qq.com --to "a@x.com,b@y.com" < message.eml

退出码：0 成功；1 投递失败（原因写到 stderr）；2 参数/输入有问题。

失败时的输出按「当前阶段（connect / login / send）+ 异常类型」分类，尽量给出
可操作的中文提示（例如 QQ/163 要用授权码、465 用 ssl、587 用 starttls），
并且**绝不打印用户名/密码/授权码**。
"""

import argparse
import smtplib
import ssl
import sys


def split_recipients(value):
    return [x for x in value.replace(",", " ").replace(";", " ").split() if x]


# 阶段 -> 中文名（错误提示里用）
PHASE_CN = {"connect": "连接", "login": "认证", "send": "投递"}


def smtp_reply_text(exc):
    """SMTP 响应异常 -> 服务端原文（如 "535 Login fail. ..."）。

    只取服务端回复的码与文本；本地凭据（用户名/密码/授权码）永远不在这里，
    也绝不要拼进任何输出。
    """
    code = getattr(exc, "smtp_code", "")
    err = getattr(exc, "smtp_error", "")
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    err = str(err).strip()
    return ("%s %s" % (code, err)).strip()


def main():
    ap = argparse.ArgumentParser(description="powerfee mail sender")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--security", default="ssl", choices=["ssl", "starttls", "none"])
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--from", dest="sender", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--tls-verify", default="1")
    ap.add_argument("--timeout", type=int, default=45)
    args = ap.parse_args()

    raw = sys.stdin.buffer.read()
    if not raw.strip():
        print("stdin 里没有邮件内容", file=sys.stderr)
        return 2
    recipients = split_recipients(args.to)
    if not recipients:
        print("收件人为空", file=sys.stderr)
        return 2

    ctx = ssl.create_default_context()
    if args.tls_verify != "1":
        # 少数自建 SMTP 用自签证书；允许显式关闭校验（配置项 mail.tls_verify=0）
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    conn = None
    # 当前阶段：connect -> login -> send，异常分类提示靠它区分
    phase = "connect"
    try:
        if args.security == "ssl":
            conn = smtplib.SMTP_SSL(args.host, args.port, timeout=args.timeout, context=ctx)
        else:
            conn = smtplib.SMTP(args.host, args.port, timeout=args.timeout)
            conn.ehlo()
            if args.security == "starttls":
                conn.starttls(context=ctx)
                conn.ehlo()
        if args.user:
            phase = "login"
            conn.login(args.user, args.password)
        phase = "send"
        # 传 bytes：不重排行尾（我们的 .eml 已经是标准 CRLF），smtplib 只做点填充
        conn.sendmail(args.sender, recipients, raw)
        try:
            conn.quit()
        except Exception:
            pass
    except smtplib.SMTPAuthenticationError as exc:
        # 服务端明确回了 535 等认证错误：把原文给出来，并提示最可能的配置错误
        print("邮件发送失败：SMTP 认证被拒绝。", file=sys.stderr)
        print("服务端原文：%s" % smtp_reply_text(exc), file=sys.stderr)
        print("提示：QQ / 163 邮箱必须填「授权码」而不是网页登录密码；QQ 邮箱的授权码是 16 位；", file=sys.stderr)
        print("      需要先在邮箱网页设置的「账户」里开启 SMTP / IMAP 服务（开启时才会给出授权码）。", file=sys.stderr)
        print("      改好后用 powerfee test-mail 重试（不要连续重试，失败次数多会被限流）。", file=sys.stderr)
        return 1
    except smtplib.SMTPServerDisconnected as exc:
        if phase == "login":
            # QQ 首次认证失败后会限流：后续连接在认证阶段被直接掐断，
            # 拿不到 535 原文 —— 必须把「认证失败」这个最可能的原因说清楚，
            # 否则很容易被误判成网络问题。
            print("邮件发送失败：SMTP 服务端在「认证」阶段主动断开连接（没有返回 535 等错误码）。", file=sys.stderr)
            print("常见原因：", file=sys.stderr)
            print("  ① 授权码不正确 —— QQ 邮箱首次认证失败后会限流，后续连接可能被直接断开，", file=sys.stderr)
            print("     所以拿不到服务端 535 原文；", file=sys.stderr)
            print("  ② 该邮箱的 SMTP 服务未开启（邮箱设置 → 账户 → 开启 SMTP/IMAP）；", file=sys.stderr)
            print("  ③ 被服务商风控（短时间多次失败重试会加重）。", file=sys.stderr)
            print("建议先用 curl / openssl s_client 手工走一遍 SMTP 对话验证授权码，确认无误后再重试；", file=sys.stderr)
            print("不要反复重试，以免加重限流。底层错误：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        else:
            print("邮件发送失败：SMTP 服务端在「%s」阶段断开了连接。底层错误：%s: %s"
                  % (PHASE_CN.get(phase, phase), type(exc).__name__, exc), file=sys.stderr)
            print("提示：检查网络与端口方向 —— 465 端口要用 ssl（mail.security=ssl），", file=sys.stderr)
            print("      587 端口要用 starttls（mail.security=starttls）；确认 DNS 能解析邮件服务器、", file=sys.stderr)
            print("      路由器能正常出网（可先 curl -v telnet://%s:%s 试连通性）。" % (args.host, args.port), file=sys.stderr)
        return 1
    except smtplib.SMTPException as exc:
        print("邮件发送失败：SMTP 错误（阶段 %s，%s）：%s"
              % (PHASE_CN.get(phase, phase), type(exc).__name__, exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 统一转成退出码交给 shell 记录
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
