# 宿舍电量哨兵 × 微信 / QQ 通知与查询

把路由器上的 **powerfee** 接进聊天软件：**余额异常时主动推消息**，**平时在聊天里查电费**。

本文只写**实测过 / 有出处**的接口，不含臆造步骤。

---

## 一、总览

```
                       ┌──────────────────────────── 路由器 (OpenWrt) ───────────────────────────┐
                       │                                                                          │
  学校电费接口  ◀──────▶  powerfee  ──(1) notify 段 webhook 推送──▶ 微信推送服务 / AstrBot         │
   (HTTPS)             │   常驻监控                                 │  (docker)                     │
                       │      ▲                                     ▼                               │
                       │      │  (2) CGI 查询端点            微信 / QQ 聊天窗口                    │
                       │      └── uhttpd :8443 ◀── 机器人查询 ── AstrBot 插件 / 任意脚本             │
                       └──────────────────────────────────────────────────────────────────────────┘
```

- **(1) 推送**：powerfee 检测到「低电量 / 进入预警区 / 充值恢复 / 监控失效 / 测试」时，
  按 UCI `notify` 段的配置 POST 一条消息给微信推送服务或 AstrBot。**单向、无回复**。
- **(2) 查询**：机器人（AstrBot 插件、脚本、手机）请求路由器上的 CGI 端点，
  拿回 JSON / 单行摘要，再转发到聊天窗口。**按需、可回复**。

两条链路互相独立：推送坏了不影响查询，查询坏了不影响推送。

### 接口约定（由 powerfee 侧提供）

**推送（UCI `config notify 'notify'`）**

| 选项 | 含义 |
|---|---|
| `enabled` | 总开关 |
| `url` | 接收 webhook 的完整地址 |
| `method` | `POST` 或 `GET` |
| `content_type` | POST 的 Content-Type（默认 `application/json`） |
| `body` | 请求体模板，支持占位符（下表） |
| `token` | 鉴权凭据（写进 header） |
| `token_header` | 放 token 的 header 名（例如 `Authorization` / `X-API-Key`） |
| `timeout` | 单次请求超时（秒） |

`body` 占位符：`{text}`（**单行摘要**，可直接嵌 JSON）、`{title}`、`{room}`、`{balance}`、
`{unit}`、`{level}`、`{reason}`、`{daily}`、`{days_left}`、`{time}`、`{device}`。
默认 body 是 `{"text":"{text}"}`。

> `notify` 段里还有两个给**查询端点**用的选项（CGI 读的是它们）：
> `http_enabled`（端点总开关）与 `http_token`（查询 token）。
> **两个都要配好**，否则端点一律 403。

**查询（uhttpd CGI，监听 `0.0.0.0:8443`，自签证书）**

```
GET https://<路由器IP>:8443/cgi-bin/powerfee?token=<http_token>&cmd=<status|brief|groups|rooms|history|log|check>[&kw=..][&limit=..][&n=..]
```

- 返回 `application/json`；token 不对 / 端点未启用 → **403**；cmd 不在白名单 → **400**
- `cmd=brief` → `{"ok":true,"text":"<单行摘要>","balance":…,"level":…,"unit":…}` —— **直接转发到聊天窗口用这个**
- 自签证书 → `curl -k`
- 路由器 IP：局域网 `192.168.1.1`；**docker 容器里也可以用 `172.17.0.1`**（宿主机网关）

---

## 二、微信：ClawBot（iLink）+ 轻量推送 API

> **懒人路线（1.0.2 起）**：装 `luci-app-powerfee` 后打开
> **服务 → 宿舍电量哨兵 → 微信推送**，页面会把本节的手工步骤（装服务 → 扫码 →
> 在微信里发一条消息激活 → 写推送配置）串成五步向导，二维码直接显示在网页上，
> 最后点「一键启用微信推送」即可（自动读 `auth.json` 的 `bot_id`/`api_token`、
> 写 UCI、commit、发一条测试消息）。原理、实现与边界见
> [`luci-wechat-page.md`](luci-wechat-page.md)；**下面这一节是手工流程与原理**，
> 出问题需要逐条排查时看它。

### 2.1 微信 ClawBot 是什么

「微信 ClawBot」是**微信官方**的 bot 通道（协议代号 **iLink**），
官方把它打包成 npm 插件 `@tencent-weixin/openclaw-weixin` 供 OpenClaw 一类宿主接入。
它的工作方式：

1. 部署一个 iLink bot 客户端 → 客户端向 `https://ilinkai.weixin.qq.com` 申请二维码；
2. **用你的微信扫码**，确认后微信与这个 bot 建立绑定（返回 `bot_token` / `bot_id`）；
3. 之后 bot 可以**给你（绑定的微信号）发消息**——消息出现在你和这个 ClawBot 的聊天窗口里；
4. bot 也可以收消息（长轮询 `getupdates`），因此能做「推送 + 查询」双向，但**推送只需要发消息**。

> 关键细节：**必须先给你的 ClawBot 发过至少一条消息**，客户端才拿到 `context_token`，
> 之后才能主动发消息（否则服务端会拒绝 / 报 context 未就绪）。这一条是实测项目源码里的行为。

### 2.2 推荐方案：`Cp0204/WeClawBot-API`

调研对比了三个真实项目：

| 项目 | 星标 | 是什么 | 适合我们吗 |
|---|---|---|---|
| **[Cp0204/WeClawBot-API](https://github.com/Cp0204/WeClawBot-API)** | 66 | Go 写的**纯推送 HTTP API**，≈10MB 内存，Docker 一条命令 | ✅ **就是它**：极轻、有 REST API、能跑在路由器 docker 里 |
| [SiverKing/weixin-ClawBot-API](https://github.com/SiverKing/weixin-ClawBot-API) | 132 | Python，把收到的微信消息转给 DeepSeek/DusAPI 做 AI 对话 | ❌ 是「聊天机器人」，没有对外推送 API |
| [fastclaw-ai/weclaw](https://github.com/fastclaw-ai/weclaw) | 1677 | 把微信接到 Claude/Codex 等 agent（ACP/CLI/HTTP） | ❌ 面向 agent 桥接，不是通知推送 |

**WeClawBot-API 的真实接口**（摘自仓库 README 与 `main.go`，已核对源码）：

- 镜像：`cp0204/weclawbot-api:latest`，容器端口 **26322**，配置持久化在 `/app/config`（`auth.json`）
- 发消息：
  - `GET  http://<host>:26322/bots/{bot_id}/messages?token={api_token}&text=Hello`
  - `POST http://<host>:26322/bots/{bot_id}/messages`（支持 `application/json`、表单、multipart）
- 鉴权：`Authorization: Bearer <api_token>` **或** `token=<api_token>`（query / body 都行）
- 发送状态：`/bots/{bot_id}/typing?status=1`（1=正在输入，2=停止）
- 返回：成功 `{"code":200,"message":"OK"}`；失败 `{"code":401,"error":"Unauthorized"}`（HTTP 状态码同步）
- 拿 `bot_id` / `api_token`：进容器控制台执行 `/bots`（也保存在 `config/auth.json`）

**部署（路由器 docker，实测容器运行环境可用）**

```sh
# 在路由器上
mkdir -p /opt/weclawbot/config
docker run -d --name weclawbot-api \
  -p 26322:26322 \
  -v /opt/weclawbot/config:/app/config \
  --restart unless-stopped \
  cp0204/weclawbot-api:latest

# 首次登录：进容器控制台，输入 /login 扫码（终端里会打印二维码）
docker exec -it weclawbot-api bot
#   /login   扫码
#   /bots    列出 bot_id 和 api_token（记下来）
```

> 容器内存约 10MB，对 1.9GB 的路由器没有压力；镜像拉取失败时按需换镜像源。
> **扫码前先在微信里给 ClawBot 发一条消息**，否则 API 会返回 `Context not ready`。

**验证**

```sh
curl -s "http://127.0.0.1:26322/bots/<bot_id>/messages?token=<api_token>&text=powerfee测试"
# {"code":200,"message":"OK"}
```

**powerfee 侧配置（照抄，替换 3 个尖括号）**

```sh
uci set powerfee.notify=notify
uci set powerfee.notify.enabled='1'
uci set powerfee.notify.url='http://127.0.0.1:26322/bots/<bot_id>/messages'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='application/json'
uci set 'powerfee.notify.body={"text":"{text}"}'
uci set powerfee.notify.token_header='Authorization'
uci set powerfee.notify.token='Bearer <api_token>'
uci set powerfee.notify.timeout='10'
uci commit powerfee
powerfee notify-test     # 立刻发一条测试推送并打印结果
```

`{text}` 是单行摘要，直接嵌进 JSON 不会破坏格式。

### 2.3 微信侧的局限（如实说）

- iLink 目前是**灰度**通道（项目 README 原话「当前灰度中，可用性待观察」），
  token 可能过期，需要重新扫码（客户端会提示 / 返回 `-14` 时重登）。
- 消息只发到「你与 ClawBot 的会话」，不是发给你的好友；这是微信官方通道的限制。
- 该项目是社区实现，**不是腾讯官方支持的产品**，别用于关键业务。

---

## 三、QQ：走 AstrBot（路由器上已有）

路由器 docker 里跑着 **AstrBot 4.28.2**（容器名 `astrbot`，WebUI `http://192.168.1.1:6185`）。
QQ 已接通（实测配置里有 `aiocqhttp`（OneBot v11，反向 WS 端口 6199）与 3 个 `qq_official` 实例）。

> **实测注意**：检查时 `aiocqhttp` 的 6199 端口**没有活跃连接**（NapCat/LLOneBot 客户端不在线），
> 最近的 QQ 流量来自 `qq_official` 实例。两条推送方式都在下面给出，按你实际在线的通道选。

### 3.1 方式①：AstrBot Open API（推荐，平台无关）

AstrBot 4.28 自带 **Open API**，端点与鉴权（核对自容器内源码
`astrbot/dashboard/api/open_api.py`、`api/auth.py`）：

- **发消息**：`POST /api/v1/im/messages`
  - 请求头：`X-API-Key: <key>`（也接受 `Authorization: ApiKey <key>`、`?api_key=<key>`）
  - 需要 API Key 的 **`im`** 权限
  - 请求体：`{"umo": "<统一消息来源>", "message": [{"type":"plain","text":"..."}]}`
- **列平台**：`GET /api/v1/im/bots`（返回平台实例 id 列表）
- **API Key 在哪拿**：WebUI（`http://192.168.1.1:6185`）→ 开放接口 / API Keys → 新建，
  勾上 `im` 权限。**Key 只在创建时显示一次**。
- **重载插件**（改了插件配置后用）：`POST /api/v1/plugins/reload`，body `{"plugin_id":"<插件目录名>"}`，
  需要 `plugin` 权限。⚠️ 实测这个接口会**重载全部插件**，要 60~120 秒才返回；
  客户端 `curl -m` 设小了会先超时（请求其实还在后台跑完），别以为失败了。

**`umo` 怎么填**：格式是 `<平台实例id>:<消息类型>:<会话id>`（源码 `MessageSession.__str__`）。

| 平台 | 场景 | umo 示例 |
|---|---|---|
| aiocqhttp | 私聊 | `default:FriendMessage:123456789`（会话 id = 对方 QQ 号） |
| aiocqhttp | 群聊 | `default:GroupMessage:987654321`（会话 id = 群号） |
| qq_official | 群聊 | `default_<平台实例id>:GroupMessage:<会话id>`（会话 id 是平台给的哈希串） |

拿 umo 最省事的办法：**在目标聊天窗口里发 `/电费 会话`**（本仓库的 AstrBot 插件提供），
它会把当前会话的 umo 原样回给你；也可以查 WebUI 的会话列表 / 数据库 `platform_message_history`。

**powerfee 侧配置（照抄）**

```sh
uci set powerfee.notify=notify
uci set powerfee.notify.enabled='1'
uci set powerfee.notify.url='http://127.0.0.1:6185/api/v1/im/messages'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='application/json'
uci set 'powerfee.notify.body={"umo":"<UMO>","message":[{"type":"plain","text":"{text}"}]}'
uci set powerfee.notify.token_header='X-API-Key'
uci set powerfee.notify.token='<API Key>'
uci set powerfee.notify.timeout='10'
uci commit powerfee
```

**验证（路由器上直接打）**

```sh
curl -s -X POST http://127.0.0.1:6185/api/v1/im/messages \
  -H 'X-API-Key: <API Key>' -H 'Content-Type: application/json' \
  -d '{"umo":"<UMO>","message":[{"type":"plain","text":"powerfee 测试消息"}]}'
# 成功: {"status":"ok", ...}
```

**更省事的验证：让 powerfee 自己推一条测试通知**（不需要手写 JSON）：

```sh
uci set powerfee.notify.enabled='1'
uci set powerfee.notify.url='http://127.0.0.1:6185/api/v1/im/messages'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='application/json'
uci set 'powerfee.notify.body={"umo":"<UMO>","message":[{"type":"plain","text":"{text}"}]}'
uci set powerfee.notify.token_header='X-API-Key'
uci set powerfee.notify.token='<API Key>'
uci set powerfee.notify.timeout='10'
uci commit powerfee
powerfee notify-test          # 成功会打印：测试通知已推送（POST http://...）
powerfee log 5                # 日志里会写：通知已推送（原因 test）：HTTP 200
```

> 实测（2026-10-07）：上面这组配置逐字可用——`powerfee notify-test` 返回 HTTP 200，
> 消息真的进了目标会话（在 AstrBot 的消息记录里能查到 `🔔 测试通知：宿舍电量哨兵（OpenWrt 版）`）。

> 容器视角：如果 powerfee 以后跑在容器里，AstrBot 地址用 `http://172.17.0.1:6185`
> 或容器名（同一 docker 网络）；现在 powerfee 跑在路由器本体上，`127.0.0.1` 即可。

### 3.2 方式②：OneBot v11 `/send_private_msg`（有 NapCat 时）

OneBot v11 标准接口，**发给 QQ 客户端自己的 HTTP API**：

```sh
curl -s -X POST 'http://<NapCat主机>:3000/send_private_msg' \
  -H 'Authorization: Bearer <access_token>' -H 'Content-Type: application/json' \
  -d '{"user_id":123456789,"message":"powerfee 测试消息"}'
```

对应 UCI：

```sh
uci set powerfee.notify.url='http://<NapCat主机>:3000/send_private_msg'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='application/json'
uci set 'powerfee.notify.body={"user_id":<你的QQ号>,"message":"{text}"}'
uci set powerfee.notify.token_header='Authorization'
uci set powerfee.notify.token='Bearer <NapCat access_token>'
uci commit powerfee
```

> ⚠️ **重要区别**：AstrBot 的 `aiocqhttp` 适配器是**反向 WS 服务端**（监听 6199，等 NapCat 连进来），
> 它本身**不提供** `/send_private_msg` 这类 HTTP API。
> 所以方式②必须打到 **NapCat/LLOneBot 自己的 HTTP 端口**（默认 3000），
> 且该端口要对路由器可达。实测路由器上 **没有** NapCat 容器，
> NapCat 应该跑在别的设备上——不在同一网段就用不了方式②，改用方式①。

### 3.3 推送文案

`{text}` 是 powerfee 生成的单行摘要（例如
`⚠️ 1号楼 A101 仅剩 8.22 度（阈值 20 度，约可用 2.6 天）`），
直接用它最省事；想分字段排版可以用 `{title}` / `{room}` / `{balance}` / `{level}` 等占位符，
但注意 JSON 字符串里不能出现裸换行——**多行文案要么用 `\n` 转义，要么只推 `{text}`**。

---

## 四、不同房间推到不同微信 / QQ 账号（1.2.0）

一台路由器监控几个房间时，可以让**每个房间推到自己的聊天窗口**：1号楼 A101 推到你的微信、
2号楼 B202 推到宿舍群 —— 靠的是「命名推送账号」（`config push_account`）+ 房间的
`notify_account` 引用。

- 1.1.0 起已经支持**每房间一条推送地址**（`config room` 里的 `notify_url` / `notify_token`）；
- 1.2.0 起更进一步：把整套配置（地址、令牌、请求体模板）写成**命名账号**，多个房间引用同一个账号；
  房间的 `notify_url` / `notify_token` 仍然**覆盖**账号里的同名字段。

```sh
# 微信（ClawBot）：一个推到你自己微信的账号
# （页面上的等价操作：微信推送 → ClawBot → 一键启用；这里是手工版）
powerfee push-account add claw_me
powerfee push-account set claw_me channel clawbot
powerfee push-account set claw_me url 'http://127.0.0.1:26322/bots/<bot_id>/messages'
powerfee push-account set claw_me method POST
powerfee push-account set claw_me content_type application/json
powerfee push-account set claw_me body '{"text":"{text}"}'
powerfee push-account set claw_me token_header Authorization
powerfee push-account set claw_me token 'Bearer <api_token>'

# QQ（AstrBot Open API）：另一个账号，推到群会话
powerfee push-account add qq_group
powerfee push-account set qq_group channel custom
powerfee push-account set qq_group url 'http://127.0.0.1:6185/api/v1/im/messages'
powerfee push-account set qq_group method POST
powerfee push-account set qq_group content_type application/json
powerfee push-account set qq_group body '{"umo":"<群会话 UMO>","message":[{"type":"plain","text":"{text}"}]}'
powerfee push-account set qq_group token_header X-API-Key
powerfee push-account set qq_group token '<AstrBot API Key>'

powerfee push-account test claw_me    # 各发一条测试消息
powerfee push-account test qq_group

# 房间绑定：room1 → 微信；room2 → QQ 群（留空 '' 改回默认 notify 段）
powerfee room-set room1 notify_account claw_me
powerfee room-set room2 notify_account qq_group
```

要点：

- **`channel` 只是分类与提示**（`serverchan` / `wecom` / `clawbot` / `custom`），实际请求由
  `url` / `method` / `content_type` / `body` / `token` / `token_header` 决定 —— AstrBot、NapCat
  这类自建地址统一选 `custom`（留空则继承 `notify` 段，按 URL 自动识别通道）；
- **`channel=clawbot` 且 `url` 留空**时用本机 ClawBot API（等价于「一键启用」写出来的那套地址）；
- **取值优先级**：房间 `notify_account`（空 → `notify` 段）为基底，账号里留空的字段继承
  `notify` 段，房间级 `notify_url` / `notify_token` 再覆盖基底；`notify_enabled=0` 该房间不推；
- 没有任何 `config push_account` 段、房间也没写引用时，行为与 1.1.0 **完全一致**；
- 地址与令牌不会回显（`push-account list` 只回掩码），`/etc/config/powerfee` 权限已是 600。

LuCI 路径：**服务 → 宿舍电量哨兵 → 微信推送 → 「推送账号」区**（新增 / 编辑 / 删除 / 发测试消息），
以及 **宿舍管理 → 编辑房间 → 「推送账号」下拉**。

---

## 五、查询侧：机器人怎么回答「查电费」

### 5.1 先确认端点通

```sh
# 路由器上
curl -k -s 'https://127.0.0.1:8443/cgi-bin/powerfee?token=<http_token>&cmd=brief'
# {"ok":true,"text":"✅ 宿舍电量当前状态：宿舍 剩余 71.00 度（阈值 20 度）","balance":71.00,"level":"ok","unit":"度"}

# docker 容器里（例如 AstrBot 容器内）
curl -k -s 'https://172.17.0.1:8443/cgi-bin/powerfee?token=<http_token>&cmd=brief'
```

各命令的真实返回形状（已实测）：

| cmd | 返回 |
|---|---|
| `brief` | `{"ok":true,"text":"<单行摘要>","balance":71.00,"level":"ok","unit":"度"}` |
| `status` | `{"version","configured","room_display","balance","level","threshold","daily","days_left","last_ok_at","last_alert_at","poll_count","interval","cooldown","mail_enabled","notify_enabled","http_enabled","service_running",...}` |
| `rooms&kw=A1&limit=3` | `{"rooms":[{"id":"1001","campus":"东区","building":"1号楼","room":"A101","balance":"71.00"},...],"shown":3,"total":3,"truncated":true}` |
| `check` | `{"ok":true,"balance":71.00,"level":"ok","reason":"","error":""}` |
| 未启用 / token 错 | HTTP 403 `{"error":"endpoint disabled"}` / `{"error":"bad token"}` |
| cmd 不在白名单 | HTTP 400 `{"error":"bad cmd"}` |

### 5.2 AstrBot 插件（本仓库提供）

`integrations/astrbot-plugin-powerfee/` —— 安装与配置见该目录 `README.md`。
装好后聊天里：

| 指令 | 说明 |
|---|---|
| `/电费` | 单行摘要（`cmd=brief` 的 `text` 直接转发） |
| `/电费 全部` | 完整状态（`cmd=status`，插件排版成多行） |
| `/电费 房间 A1` | 房间搜索（`cmd=rooms&kw=A1`） |
| `/电费 历史` / `/电费 日志` | `cmd=history` / `cmd=log` |
| `/电费 检查` | `cmd=check`，**会真的触发一次查询与告警推送** |
| `/电费 会话` | 显示本会话 umo（配置推送时用） |

### 5.3 任意脚本 / 其它机器人

任何能发 HTTP 的机器人框架都可以：请求 `cmd=brief`，把返回 JSON 的 `text` 字段原样转发即可。
返回是 `application/json`，`ok:false` 时看 `msg`。

---

## 六、安全

- **查询端点只走局域网**：uhttpd 的 8443 不要做端口映射到公网；只允许 LAN / docker 网段访问。
- **必须设 token**：`http_token` 留空等于把电费数据开放给整个局域网（含访客 Wi-Fi）。
- **`cmd=check` 有副作用**：真的会去查学校接口，并可能触发低电量 / 恢复告警推送；不要做定时轮询轰炸。
- **自签证书**：curl 用 `-k`，插件里把「校验 HTTPS 证书」关掉；换正式证书后记得打开。
- **API Key / 微信 api_token 当密码保管**：`/etc/config/powerfee` 权限已是 600，
  别把带 token 的配置提交进公开仓库。
- **推送频率**：powerfee 自带冷却（默认同档位 180 分钟一次），别在 `notify` 里再叠加重试风暴。

---

## 七、排查

| 现象 | 排查 |
|---|---|
| 微信收不到 | `docker logs weclawbot-api`；先手动 curl 一次 `/bots/{id}/messages`；`Context not ready` = 没先给 ClawBot 发过消息 |
| QQ 收不到（方式①） | curl 看返回：401 = Key 错/无 `im` 权限；`Bot not found or not running for platform` = umo 里的平台 id 不对；`Invalid umo` = 格式不对（必须 3 段） |
| QQ 收不到（方式②） | 确认 NapCat 的 HTTP 端口从路由器可达：`curl http://<napcat>:3000/get_status` |
| 端点 403 | `token` 不对，或 CGI 端点未启用（`powerfee.notify.http_enabled=0` / `http_token` 为空都会 403，返回 `{"error":"endpoint disabled"}` 或 `{"error":"bad token"}`） |
| 端点 400 | `cmd` 不在白名单（只允许 status/brief/groups/rooms/history/log/check） |
| 端点连不上 | 路由器上 `netstat -lntp \| grep 8443`；docker 里改用 `172.17.0.1` |
| 指令没反应 | AstrBot WebUI 看插件是否加载、日志有无异常；白名单是否放行了你的 QQ 号 |
| 重载插件「超时」 | 该接口会重载全部插件，60~120 秒才返回；curl 超时不代表失败，过一分钟看日志 `Loading plugin ...` |

---

## 八、已验证范围（2026-10-07，真机）

**用的是真端点**（`/www/cgi-bin/powerfee` + `powerfee json`，另一个代理实现的那套），不是 mock：

| 链路 | 结果 |
|---|---|
| `curl -k .../cgi-bin/powerfee?cmd=brief` | 真实返回 `{"ok":true,"text":"✅ 宿舍电量当前状态：宿舍 剩余 71.00 度（阈值 20 度）","balance":71.00,"level":"ok","unit":"度"}` |
| AstrBot 插件 `/电费`（走 Open API chat 事件管线） | 回复同上单行摘要 |
| `/电费 全部` | 真实 `json status` 排版：余额 71 度 / ✅ 充足 / 阈值 20 度 / 累计查询 4 次 / 上次查询成功 2026-10-07 16:53:44 … |
| `/电费 房间 A1` | 真实 `json rooms` 排版：搜到 3 个房间，A101 —— 71 度 … |
| `/电费 历史 5` | 端点当时无历史采样，插件回「没有历史数据。」（错误分支正确） |
| `/电费 会话` | 返回真实 umo（umo 三段式已核对） |
| 非白名单用户 | 「🚫 你没有查询宿舍电费的权限。」 |
| 端点在 `http_enabled=0` 时 | 插件回「❌ 端点拒绝访问（403）：token 不对，或查询端点在路由器上没启用」（真实 403） |
| **推送**：`powerfee notify-test` → AstrBot Open API | HTTP 200；消息真的落进目标会话（AstrBot 消息记录里查到「🔔 测试通知：宿舍电量哨兵（OpenWrt 版）」） |

**没验证的部分**（如实说明）：

- **微信 ClawBot 推送**：本机与路由器都没有部署 `weclawbot-api`，也没扫码登录，
  所以微信那条链路**只核对了上游源码与 README（端点、参数、返回体、鉴权方式）**，
  没有真机联调。落地前请按 §2.2 部署后用 `curl` 打一次。
- **QQ 真机发消息**：AstrBot 的 `aiocqhttp`（OneBot 反向 WS）当前**没有活跃连接**，
  推送测试是通过 AstrBot Open API 打到 **webchat 会话**验证的（同一条 `/api/v1/im/messages` 链路）。
  目标换成 QQ 的 umo 即可，但要先确认那个平台在线。
- **`cmd=check`**：会真的触发查询与告警，为避免打扰没跑；`cmd=brief/status/rooms/history` 都已实测。

---

## 九、出处（可自行核对）

- WeClawBot-API：<https://github.com/Cp0204/WeClawBot-API>（README + `main.go`，端点/参数/返回体逐条核对）
- 微信官方 iLink 插件（npm）：<https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin>
- 另外两个 ClawBot 生态项目：<https://github.com/SiverKing/weixin-ClawBot-API>、
  <https://github.com/fastclaw-ai/weclaw>
- AstrBot：<https://github.com/AstrBotDevs/AstrBot>；本文的 API 细节核对自**路由器容器内的 4.28.2 源码**：
  `astrbot/dashboard/api/open_api.py`（`/api/v1/im/messages`）、
  `astrbot/dashboard/api/auth.py`（`X-API-Key` / `ApiKey` / `?api_key=`）、
  `astrbot/dashboard/api/router.py`（前缀 `/api/v1`）、
  `astrbot/core/platform/message_session.py`（umo 三段式）、
  `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py`（会话 id = QQ 号/群号）
- OneBot v11 标准：<https://github.com/botuniverse/onebot-11>
