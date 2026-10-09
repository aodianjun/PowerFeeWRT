"""宿舍电量哨兵（powerfee）· AstrBot 查询插件。

在聊天窗口里用 ``/电费`` 系列指令查询路由器上 powerfee 的电费余额。

- 只读：只调用 powerfee 的 CGI 查询端点（``cmd=brief/status/rooms/history/log/check``）
- 不硬编码任何地址 / token：端点与 token 都在插件配置里（默认空，未配置时给出提示）
- 白名单：默认只允许 AstrBot 管理员；可在配置里追加用户 / 群 ID

适配 AstrBot v4.28.x（插件 API：``astrbot.api`` / ``astrbot.api.event.filter``）。
"""

from __future__ import annotations

import json
import ssl
from typing import Any

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_ID = "astrbot_plugin_powerfee"
COMMAND_NAMES = ("电费", "查电费", "powerfee")

HELP_TEXT = """宿舍电量哨兵 · 可用指令
/电费              查询当前余额（单行摘要）
/电费 全部         查询完整状态（房间、余额、档位、日均用量…）
/电费 房间 <关键词> 搜索房间（按房间号 / 楼栋 / 校区）
/电费 历史 [条数]   最近的历史采样与日均估算
/电费 日志 [行数]   路由器上 powerfee 的运行日志
/电费 检查         让路由器立刻查一次电费（可能触发告警推送）
/电费 会话         显示本会话的 umo（配置推送时用）
/电费 帮助         显示这条帮助
"""

LEVEL_LABELS = {
    "ok": "✅ 充足",
    "warn": "⚠️ 预警",
    "low": "🔴 不足",
    "unknown": "❓ 未知",
}

# status 返回里的常见字段 -> 中文标签（顺序即显示顺序）
# 字段名对齐 powerfee json status 的真实输出（v1.0.0）
STATUS_FIELDS: tuple[tuple[str, str], ...] = (
    ("room_display", "房间"),
    ("room", "房间"),
    ("room_num", "房间编号"),
    ("campus", "校区"),
    ("building", "楼栋"),
    ("balance", "当前余额"),
    ("unit", "单位"),
    ("level", "状态"),
    ("reason", "原因"),
    ("threshold", "告警阈值"),
    ("warn_ratio", "预警倍数"),
    ("daily", "日均用量"),
    ("days_left", "预计可用"),
    ("last_ok_at", "上次查询成功"),
    ("last_alert_at", "上次提醒"),
    ("last_alert_reason", "提醒原因"),
    ("poll_count", "累计查询"),
    ("interval", "查询间隔"),
    ("cooldown", "重复提醒冷却"),
    ("stale_hours", "监控失效阈值"),
    ("last_error", "最近错误"),
    ("error", "错误"),
    ("msg", "信息"),
    ("notify_warn", "预警提醒"),
    ("notify_recovery", "恢复提醒"),
    ("notify_error", "失效提醒"),
    ("mail_enabled", "邮件提醒"),
    ("mail_ready", "邮件通道就绪"),
    ("mail_to", "收件人"),
    ("mail_host", "SMTP 服务器"),
    ("mail_port", "SMTP 端口"),
    ("mail_security", "SMTP 加密"),
    ("mail_transport", "投递方式"),
    ("notify_enabled", "推送启用"),
    ("notify_method", "推送方式"),
    ("http_enabled", "查询端点启用"),
    ("service_running", "服务运行中"),
    ("enabled", "监控启用"),
    ("configured", "已选定房间"),
    ("api_configured", "学校接口已配置"),
    ("version", "版本"),
    ("time", "时间"),
    ("device", "设备"),
)

# 这些键在 status 里由别的形式呈现，不再单独列一行
SKIP_KEYS = {
    "ok",
    "text",
    "title",
    "history",
    "samples",
    "log",
    "rooms",
    "groups",
    "api_url",  # 又长又是学校内部地址，聊天窗口里不显示
}

# 这些字段的值是布尔，显示成 是 / 否
BOOL_FIELDS = {
    "notify_warn",
    "notify_recovery",
    "notify_error",
    "mail_enabled",
    "mail_ready",
    "notify_enabled",
    "http_enabled",
    "service_running",
    "enabled",
    "configured",
    "api_configured",
}

# 这些字段的值是秒，显示成人话
SECONDS_FIELDS = {"interval"}
# 这些字段的值是分钟
MINUTE_FIELDS = {"cooldown"}
# 这些字段是 epoch 秒的时间戳
TIMESTAMP_FIELDS = {"last_ok_at", "last_alert_at"}


class PowerFeeError(Exception):
    """查询端点返回的错误（网络错误、鉴权失败、返回体不是 JSON 等）。"""


def _as_str_list(value: Any) -> list[str]:
    """把配置里的列表 / 逗号分隔字符串 / 单个值统一成字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("，", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any) -> str:
    num = _num(value)
    if num is None:
        return str(value)
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:.2f}".rstrip("0").rstrip(".")


async def fetch_powerfee(
    endpoint: str,
    token: str,
    cmd: str,
    *,
    verify_tls: bool = False,
    timeout: float = 15.0,
    **extra: Any,
) -> dict:
    """调用 powerfee 查询端点，返回解析后的 JSON dict。

    端点形如 ``https://192.168.1.1:8443/cgi-bin/powerfee``，
    请求 ``GET ?token=..&cmd=..[&kw=..][&limit=..][&n=..]``。
    """
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise PowerFeeError(
            "还没有配置查询端点。请在 AstrBot 插件配置里填写「查询端点 URL」"
            "（例如 https://192.168.1.1:8443/cgi-bin/powerfee）"
        )

    params: dict[str, str] = {"cmd": cmd}
    if token:
        params["token"] = token
    for key, value in extra.items():
        if value is not None and value != "":
            params[key] = str(value)

    if verify_tls:
        ssl_arg: Any = ssl.create_default_context()
    else:
        # 路由器 uhttpd 用自签证书（等价于 curl -k）
        ssl_arg = False

    client_timeout = aiohttp.ClientTimeout(total=float(timeout))
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(endpoint, params=params, ssl=ssl_arg) as resp:
                body = await resp.text(errors="replace")
                if resp.status == 403:
                    raise PowerFeeError(
                        "端点拒绝访问（403）：token 不对，或查询端点在路由器上没启用"
                    )
                if resp.status == 400:
                    raise PowerFeeError(f"端点说这个命令不认识（400）：cmd={cmd}")
                if resp.status != 200:
                    raise PowerFeeError(f"端点返回 HTTP {resp.status}：{body[:200]}")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise PowerFeeError(
                        f"端点返回的不是 JSON（{exc}）：{body[:200]}"
                    ) from exc
    except aiohttp.ClientError as exc:
        raise PowerFeeError(f"连不上查询端点：{exc}") from exc
    except TimeoutError as exc:
        raise PowerFeeError(f"查询端点超时（{timeout:g} 秒）") from exc

    if not isinstance(data, dict):
        raise PowerFeeError(f"端点返回的 JSON 不是对象：{str(data)[:200]}")
    return data


def _error_line(data: dict) -> str | None:
    """识别端点返回的错误：ok:false，或（没有 ok 时的）error/msg 键。"""
    if data.get("ok") is False:
        msg = data.get("msg") or data.get("error") or data.get("message") or "未知错误"
        return f"❌ 查询失败：{msg}"
    if "ok" not in data:
        msg = data.get("error") or data.get("msg") or data.get("message")
        if msg:
            return f"❌ 查询失败：{msg}"
    return None


def format_brief(data: dict) -> str:
    """cmd=brief 的返回：直接转发单行摘要。"""
    err = _error_line(data)
    if err:
        return err
    text = str(data.get("text") or "").strip()
    if text:
        return text
    # 端点没给 text 时，用常见字段兜底拼一行
    parts: list[str] = []
    if data.get("room"):
        parts.append(str(data["room"]))
    if data.get("balance") is not None:
        unit = data.get("unit") or ""
        parts.append(f"余额 {_fmt_number(data['balance'])}{unit}")
    level = str(data.get("level") or "").lower()
    if level:
        parts.append(LEVEL_LABELS.get(level, level))
    if parts:
        return "宿舍电量：" + " · ".join(parts)
    return "查询成功，但端点没有返回可显示的内容。"


def _fmt_duration(seconds: Any) -> str:
    num = _num(seconds)
    if num is None:
        return str(seconds)
    if num < 90:
        return f"{int(num)} 秒"
    minutes = num / 60
    if minutes < 90:
        return f"约 {_fmt_number(minutes)} 分钟"
    return f"约 {_fmt_number(minutes / 60)} 小时"


def _fmt_minutes(value: Any) -> str:
    num = _num(value)
    if num is None:
        return str(value)
    if num < 90:
        return f"约 {_fmt_number(num)} 分钟"
    return f"约 {_fmt_number(num / 60)} 小时"


def _fmt_timestamp(value: Any) -> str:
    """epoch 秒 → 本地时间；不是时间戳就原样返回。"""
    num = _num(value)
    if num is None or num < 1_000_000_000:
        return str(value)
    try:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(num).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return str(value)


def format_status(data: dict, *, unit: str = "度") -> str:
    """cmd=status 的返回：排版成聊天窗口友好的多行文本。"""
    err = _error_line(data)
    if err:
        return err

    text = str(data.get("text") or "").strip()
    lines: list[str] = []
    seen_labels: set[str] = set()
    for key, label in STATUS_FIELDS:
        if key not in data or data[key] in (None, "") or label in seen_labels:
            continue
        value = data[key]
        if key in BOOL_FIELDS:
            value = "是" if value else "否"
        elif key == "level":
            value = LEVEL_LABELS.get(str(value).lower(), value)
        elif key in ("balance", "threshold"):
            value = f"{_fmt_number(value)} {unit}".strip()
        elif key == "daily":
            value = f"{_fmt_number(value)} {unit}/天"
        elif key == "days_left":
            value = f"约 {_fmt_number(value)} 天"
        elif key in SECONDS_FIELDS:
            value = _fmt_duration(value)
        elif key in MINUTE_FIELDS:
            value = _fmt_minutes(value)
        elif key in TIMESTAMP_FIELDS:
            value = _fmt_timestamp(value)
        elif key == "poll_count":
            value = f"{value} 次"
        elif key == "stale_hours":
            value = f"{value} 小时"
        # 同一标签只显示第一次出现的（例如 room_display 与 room 都叫「房间」）
        seen_labels.add(label)
        lines.append(f"{label}：{value}")

    for key, value in data.items():
        if key in SKIP_KEYS or key in {k for k, _ in STATUS_FIELDS}:
            continue
        if isinstance(value, (dict, list)) or value is None or value == "":
            continue
        lines.append(f"{key}：{value}")

    header = text or "宿舍电量 · 完整状态"
    if lines:
        return header + "\n" + "\n".join(lines)
    return header


def format_rooms(data: dict, *, max_rooms: int = 15, unit: str = "度") -> str:
    """cmd=rooms 的返回：列出房间与余额。"""
    err = _error_line(data)
    if err:
        return err

    rooms: Any = data.get("rooms")
    if rooms is None:
        # 容错：取返回里第一个列表值
        for value in data.values():
            if isinstance(value, list):
                rooms = value
                break
    if not isinstance(rooms, list):
        text = str(data.get("text") or "").strip()
        return text or "没有搜到房间。"

    if not rooms:
        return "没有搜到匹配的房间。"

    lines: list[str] = []
    for item in rooms[: max(1, int(max_rooms))]:
        if not isinstance(item, dict):
            lines.append(str(item))
            continue
        name = (
            item.get("room")
            or item.get("name")
            or item.get("room_num")
            or item.get("id")
            or "?"
        )
        balance = item.get("balance")
        if balance is None:
            balance = item.get("powerBalance")
        item_unit = item.get("unit") or unit
        bits = [str(name)]
        for key in ("building", "campus"):
            if item.get(key):
                bits.append(str(item[key]))
        line = " · ".join(bits)
        if balance is not None:
            line += f" —— {_fmt_number(balance)} {item_unit}"
        lines.append(line)

    # total 是端点给的总数（可能大于返回的条数）
    try:
        total = int(data.get("total", len(rooms)))
    except (TypeError, ValueError):
        total = len(rooms)
    shown = len(rooms)
    header = f"搜到 {total} 个房间：" + ("" if total == shown else f"（显示前 {shown} 个）")
    if total > shown:
        lines.append("…还有更多，可在配置里调大显示条数")
    return header + "\n" + "\n".join(lines)


def format_history(data: dict, *, max_items: int = 12, unit: str = "度") -> str:
    """cmd=history 的返回：最近采样列表。"""
    err = _error_line(data)
    if err:
        return err

    text = str(data.get("text") or "").strip()
    items: Any = data.get("history") or data.get("samples") or data.get("items")
    if not isinstance(items, list):
        return text or "没有历史数据。"

    lines: list[str] = []
    for item in items[: max(1, int(max_items))]:
        if isinstance(item, dict):
            when = item.get("time") or item.get("ts") or item.get("timestamp") or ""
            balance = item.get("balance")
            if balance is None:
                balance = item.get("powerBalance")
            piece = str(when).strip()
            if balance is not None:
                piece = f"{piece}  {_fmt_number(balance)} {item.get('unit') or unit}".strip()
            lines.append(piece or json.dumps(item, ensure_ascii=False))
        else:
            lines.append(str(item))

    header = text or f"最近 {len(lines)} 条采样："
    return header + "\n" + "\n".join(lines)


def format_log(data: dict, *, max_items: int = 20) -> str:
    """cmd=log 的返回：日志行。"""
    err = _error_line(data)
    if err:
        return err

    lines = data.get("log") or data.get("lines") or data.get("text")
    if isinstance(lines, list):
        lines = "\n".join(str(x) for x in lines[: max(1, int(max_items))])
    if not lines:
        return "没有日志。"
    return str(lines)


@register(
    PLUGIN_ID,
    "PowerFeeWRT",
    "宿舍电量哨兵：在聊天里查询路由器上 powerfee 的电费余额与房间列表（只读）。",
    "v1.0.0",
)
class PowerFeePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def initialize(self) -> None:
        endpoint = str(self.config.get("endpoint", "") or "").strip()
        if endpoint:
            logger.info(f"[powerfee] 查询端点：{endpoint}")
        else:
            logger.warning("[powerfee] 还没配置查询端点，/电费 会提示未配置")

    # ---------------------------------------------------------------- helpers
    def _endpoint(self) -> str:
        return str(self.config.get("endpoint", "") or "").strip()

    def _token(self) -> str:
        return str(self.config.get("token", "") or "").strip()

    def _unit(self) -> str:
        return str(self.config.get("default_unit", "") or "度").strip() or "度"

    def _allowed(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """白名单判定：管理员 / 用户白名单 / 群白名单，三者之一命中即放行。"""
        allow_admins = bool(self.config.get("allow_admins", True))
        allow_users = _as_str_list(self.config.get("allow_users"))
        allow_groups = _as_str_list(self.config.get("allow_groups"))

        if allow_admins and event.is_admin():
            return True, ""
        if allow_users and str(event.get_sender_id()) in allow_users:
            return True, ""
        group_id = str(event.get_group_id() or "")
        if allow_groups and group_id and group_id in allow_groups:
            return True, ""

        if not allow_admins and not allow_users and not allow_groups:
            return False, (
                "这个插件还没配置使用白名单。请在 AstrBot 的插件配置里把管理员开关打开，"
                "或把你的用户 ID / 群 ID 填进白名单。"
            )
        return False, "你没有查询宿舍电费的权限。"

    async def _query(self, cmd: str, **extra: Any) -> str:
        timeout = float(self.config.get("timeout", 15) or 15)
        data = await fetch_powerfee(
            self._endpoint(),
            self._token(),
            cmd,
            verify_tls=bool(self.config.get("verify_tls", False)),
            timeout=timeout,
            **extra,
        )
        max_rooms = int(self.config.get("max_rooms", 15) or 15)
        if cmd == "brief":
            return format_brief(data)
        if cmd == "status":
            return format_status(data, unit=self._unit())
        if cmd == "rooms":
            return format_rooms(data, max_rooms=max_rooms, unit=self._unit())
        if cmd == "history":
            return format_history(data, max_items=max_rooms, unit=self._unit())
        if cmd == "log":
            return format_log(data)
        if cmd == "check":
            return format_status(data, unit=self._unit())
        return json.dumps(data, ensure_ascii=False, indent=2)[:1800]

    # --------------------------------------------------------------- commands
    @filter.command("电费", alias={"查电费", "powerfee"})
    async def powerfee_cmd(self, event: AstrMessageEvent):
        """查询宿舍电费：/电费 [全部|房间 <关键词>|历史|日志|检查|会话|帮助]"""
        allowed, reason = self._allowed(event)
        if not allowed:
            yield event.plain_result(f"🚫 {reason}")
            return

        raw = event.get_message_str().strip()
        # 去掉指令名本身，取剩余参数
        args = raw
        first, _, rest = raw.partition(" ")
        if first in COMMAND_NAMES:
            args = rest.strip()
        elif " " in raw and raw.split(" ", 1)[0] in COMMAND_NAMES:
            args = raw.split(" ", 1)[1].strip()

        try:
            if not args or args in ("查询", "余额"):
                reply = await self._query("brief")
            elif args in ("帮助", "help", "?", "？"):
                reply = HELP_TEXT
            elif args in ("全部", "详情", "状态", "status", "all"):
                reply = await self._query("status")
            elif args in ("会话", "umo"):
                reply = (
                    "本会话的 umo：\n"
                    f"{event.unified_msg_origin}\n"
                    "（配置 powerfee 的 notify 推送时，umo 要填这一串）"
                )
            elif args.startswith("房间") or args.startswith("搜索"):
                keyword = args[2:].strip()
                if not keyword:
                    reply = "用法：/电费 房间 <关键词>（房间号 / 楼栋 / 校区）"
                else:
                    reply = await self._query("rooms", kw=keyword)
            elif args.startswith("历史"):
                tail = args[2:].strip()
                n = int(tail) if tail.isdigit() else None
                reply = await self._query("history", **({"n": n} if n else {}))
            elif args.startswith("日志"):
                tail = args[2:].strip()
                n = int(tail) if tail.isdigit() else None
                reply = await self._query("log", **({"n": n} if n else {}))
            elif args in ("检查", "check"):
                reply = await self._query("check")
                reply = "（已让路由器立即查询一次，若余额异常会推送告警）\n" + reply
            else:
                reply = f"不认识「{args}」。\n\n" + HELP_TEXT
        except PowerFeeError as exc:
            reply = f"❌ {exc}"
        except Exception as exc:  # noqa: BLE001 - 插件出错也不能把异常抛回框架
            logger.error(f"[powerfee] 查询失败：{exc!r}", exc_info=True)
            reply = f"❌ 插件内部错误：{exc}"

        yield event.plain_result(reply)
