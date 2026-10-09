# 宿舍电量哨兵 · AstrBot 插件（astrbot_plugin_powerfee）

在 QQ / 微信等聊天窗口里用 `/电费` 查询路由器上 **powerfee** 的电费余额。

- **只读**：只调用 powerfee 的 CGI 查询端点（`cmd=brief/status/rooms/history/log/check`）
- **不硬编码**：端点与 token 都在插件配置里，默认空；没配时指令会提示怎么配
- **白名单**：默认只允许 AstrBot 管理员；可另加用户 / 群白名单

适配 **AstrBot v4.28.x**（在路由器上实测：`docker exec astrbot pip show AstrBot` → 4.28.2）。

## 指令

| 指令 | 说明 | 调用的端点命令 |
|---|---|---|
| `/电费` | 单行摘要（余额 / 档位 / 预计可用） | `cmd=brief` |
| `/电费 全部` | 完整状态，多行排版 | `cmd=status` |
| `/电费 房间 <关键词>` | 按房间号 / 楼栋 / 校区搜索 | `cmd=rooms&kw=..` |
| `/电费 历史 [条数]` | 最近采样与日均估算 | `cmd=history&limit=..` |
| `/电费 日志 [行数]` | 路由器上 powerfee 的运行日志 | `cmd=log&n=..` |
| `/电费 检查` | 让路由器**立刻查一次**（余额异常会触发告警推送） | `cmd=check` |
| `/电费 会话` | 显示本会话的 umo（配置推送时要用） | 不发请求 |
| `/电费 帮助` | 帮助 | 不发请求 |

别名：`/查电费`、`/powerfee`。

## 安装

AstrBot 的插件目录是容器里的 `/AstrBot/data/plugins/`，
在路由器上对应的宿主目录是 **`/opt/astrbot/data/plugins/`**（docker bind mount，已确认）。

```sh
# 1) 把插件目录拷进 AstrBot 插件目录（在路由器上执行）
cp -r /tmp/astrbot-plugin-powerfee /opt/astrbot/data/plugins/astrbot_plugin_powerfee

# 2) 让 AstrBot 加载它（二选一）
#    a. WebUI：插件管理 → 找到「宿舍电量哨兵」→ 重载
#    b. Open API：
#       curl -s -X POST http://127.0.0.1:6185/api/v1/plugins/reload \
#            -H "X-API-Key: <你的 API Key>" -H 'Content-Type: application/json' \
#            -d '{"plugin_id":"astrbot_plugin_powerfee"}'
```

> ⚠️ **重载会重载全部插件，要 60~120 秒才返回**（实测 18 个插件约 90 秒）。
> 客户端 `curl -m 120` 可能先超时，但请求会在后台跑完——
> 过一分钟看日志里有没有 `Loading plugin astrbot_plugin_powerfee ...` 与
> `Plugin astrbot_plugin_powerfee (v1.0.0) by ...` 就知道成功没有。
> API Key 在 WebUI 的「开放接口 / API Keys」里建，需要 `plugin` 权限（重载）与 `im` 权限（推送）。

> 本版本（4.28.2）默认**不会**监视插件目录自动热重载
> （需要容器环境变量 `ASTRBOT_RELOAD=1` 才启用 watchfiles 监视），
> 所以放好文件后必须走上面的「重载」。
> 重载是 AstrBot 自带能力，**不需要重启容器**。

3) 打开 WebUI（`http://192.168.1.1:6185`）→ 插件 → 宿舍电量哨兵 → 配置：

| 配置项 | 说明 |
|---|---|
| `endpoint` | 查询端点 URL，例如 `https://192.168.1.1:8443/cgi-bin/powerfee` |
| `token` | 端点 token（路由器 UCI 的 `http_token`） |
| `verify_tls` | 自签证书保持关闭（等价 `curl -k`） |
| `allow_admins` | 默认开：AstrBot 管理员可用 |
| `allow_users` / `allow_groups` | 额外白名单（填 QQ 号 / 群号字符串） |
| `max_rooms` | 列表最多显示多少条（默认 15） |

> 插件配置保存在 `/opt/astrbot/data/config/astrbot_plugin_powerfee_config.json`，
> 也可以在容器里直接改这个文件再重载插件。

## 卸载（一条命令 + 重载）

```sh
# 删插件目录与插件配置，然后让 AstrBot 重载（不重启容器）
rm -rf /opt/astrbot/data/plugins/astrbot_plugin_powerfee \
       /opt/astrbot/data/config/astrbot_plugin_powerfee_config.json
curl -s -X POST http://127.0.0.1:6185/api/v1/plugins/reload \
     -H "X-API-Key: <你的 API Key>" -H 'Content-Type: application/json' \
     -d '{"plugin_id":"astrbot_plugin_powerfee"}'
```

（也可以删完目录后在 WebUI 的「插件管理」里点一次「重载」。）
重载后该插件从内存移除，`/电费` 指令随之消失；**不影响其它插件**。

## 已验证（2026-10-07，真机）

在 owner 的 AstrBot 4.28.2（路由器 docker）上实测：

- 安装后日志出现 `Loading plugin astrbot_plugin_powerfee ...` 与
  `Plugin astrbot_plugin_powerfee (v1.0.0) by PowerFeeWRT: ...`，无异常堆栈；
  其余 17 个原有插件照常加载（`astrbot_plugin_knowledge_base` 的
  `faiss_store` 报错是**安装本插件之前就存在**的，日志里从 9 月 29 日起就有）。
- `/电费` → 真端点返回 `✅ 宿舍电量当前状态：宿舍 剩余 71.00 度（阈值 20 度）`
- `/电费 全部`、`/电费 房间 A1`（搜到 3 个房间）、`/电费 会话`、`/电费 帮助` 均正常
- 非白名单用户被拒绝；端点在 `http_enabled=0` 时提示「端点拒绝访问（403）…」
- 卸载（`rm -rf` + 重载）后 `/电费` 不再被插件接管，插件列表回到原状

## 已知限制

- **`/电费 检查` 有副作用**：会让路由器真的去学校接口查一次，余额异常时会触发 powerfee 的告警推送（低电量 / 恢复等）。不想被推送就别用这条。
- **私聊 / 群聊都可用，但默认只放行管理员**：AstrBot 的 `admins_id` 默认是 `astrbot` 这类账号名，QQ 场景下往往匹配不上，**请把 owner 的 QQ 号填进 `allow_users`**，否则会收到「没有权限」。
- **`/电费 全部` 的排版依赖端点返回的字段名**：插件对 `balance/level/daily/days_left/room/...` 做了中文标签映射，未知字段原样列出；端点若改字段名，排版会退化但不会报错。
- **不含推送功能**：通知推送由 powerfee 的 `notify` 段直接调 AstrBot Open API 完成，不经过本插件（见 `docs/notify-wechat-qq.md`）。
- **指令回复是纯文本**：没有做图片渲染 / markdown 表格（QQ 官方机器人对 markdown 支持有限）。
- 端点是 HTTPS 自签证书，插件按配置关闭证书校验；如路由器换了正式证书可把 `verify_tls` 打开。

## 自测（不依赖 AstrBot 事件）

把下面的脚本存成 `/tmp/pf_selftest.py` 再 `docker cp` 进容器执行：

```python
import asyncio
import sys

sys.path.insert(0, "/AstrBot/data/plugins/astrbot_plugin_powerfee")
from main import fetch_powerfee, format_brief, format_status  # noqa: E402

data = asyncio.run(
    fetch_powerfee(
        "https://192.168.1.1:8443/cgi-bin/powerfee",
        "TOKEN",
        "brief",
        timeout=10,
    )
)
print(format_brief(data))
```

```sh
docker cp /tmp/pf_selftest.py astrbot:/tmp/pf_selftest.py
docker exec astrbot python /tmp/pf_selftest.py
```
