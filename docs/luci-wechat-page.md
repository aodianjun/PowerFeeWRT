# LuCI「微信推送」页：通道选择器、五步向导与实现说明

`luci-app-powerfee` 1.0.3 起，**服务 → 宿舍电量哨兵 → 微信推送** 这个页面顶部多了
一个**推送通道选择器**：**Server酱 / 企业微信群机器人 / ClawBot / 自定义 webhook / 关闭**。
每个通道填好参数点一次「启用并测试」就完事（写 UCI → commit → 立刻发一条测试消息，
结果连服务端原文一起显示）；**ClawBot** 那一格是 1.0.2 就有的五步向导
（**装服务 → 扫码 → 激活 → 开推送**），每屏只显示当前该做的那一件事。

**1.2.0 起**，状态卡下面还多了一块**「推送账号」区**：命名推送账号（`config push_account`）
的列表与增删改、发测试消息 —— 配合「宿舍管理」页房间编辑弹窗里的「推送账号」下拉，
可以把不同房间绑到不同的推送目标（不同微信 / QQ / 群机器人）上，见下文。

ClawBot 是什么、为什么必须"先给机器人发一条消息"、手工用 curl 怎么配，
见 [`notify-wechat-qq.md`](notify-wechat-qq.md)；Server酱 / 企业微信的照抄配置见
[`notify-webhook.md`](notify-webhook.md)。本文只讲这个页面（以及它背后的
`powerfee push` / `powerfee wechat` 命令）怎么用、怎么实现的、边界在哪。

---

## 一、页面长什么样

从上到下四段：

### ① 状态卡

| 显示 | 来源 |
|---|---|
| **推送通道**（Server酱 / 企业微信群机器人 / ClawBot / 自定义 webhook / 未配置） | `powerfee push status` 的 `channel` + `channel_name` |
| **推送开关**（已开启 / 已关闭） | `enabled` |
| **推送地址**（**掩码**：`https://sctapi.ftqq.com/SCT1…（已保存，34 位）.send`） | `url_masked` |
| **密钥**（**掩码或提示**：`SCT1…（已保存，34 位）` / `还没填 SendKey` / `Bot xxx 的 api_token 已配置（已隐藏）`） | `key_hint` |
| **最近一次推送**（`2026-10-07 21:44:05 通知已推送（原因 test）：HTTP 200`） | `last_push`（powerfee 日志里最后一条推送结果） |

下面三个按钮：**「发测试消息」**（`powerfee push test`，只发不改配置）、**「刷新状态」**、
**「去设置页微调」**。发测试失败时会把原因（含服务端原文）直接弹出来。

### ② 推送账号（1.2.0）

命名推送账号（`config push_account`）的列表，每行：

| 显示 | 来源 |
|---|---|
| **名称** | `json push-accounts` 里账号的段名（`name`） |
| **通道**（Server酱 / 企业微信群机器人 / ClawBot / 自定义 webhook / 跟随默认） | 账号自己写的 `raw.channel`（留空时显示「跟随默认（…）」，括号里是继承解析后的 `effective.channel_name`） |
| **地址（掩码）** | `effective.url_masked`；ClawBot 且地址留空时显示「本机 ClawBot API」 |
| **状态** | `enabled` + `usable` / `issue`（引擎回的问题说明直接显示） |
| **用于** | `used_by`（引用这个账号的房间段名） |

按钮：**「添加推送账号」「刷新列表」**；每行是**「编辑」「发测试消息」「删除」**。
编辑弹窗里的字段与 `notify` 段一一对应（`channel` / `url` / `method` / `content_type` /
`body` / `token` / `token_header` / `timeout` / `bot_id`），**留空 = 继承 `notify` 段**；
令牌只显示「已设置」掩码，输入框只收新值（留空 = 不修改，勾选「清除」才清掉）。
这一块走的是 `powerfee json push-accounts` 与 `powerfee push-account add|set|remove|test`，
与通道选择器同一条 CLI 路线（同样不碰查询令牌）；房间侧在
**宿舍管理 → 编辑房间 → 「推送账号」** 下拉里绑定。

### ③ 通道选择器

五个按钮，当前配置的那个后面带 `●`；点一下换下面的表单（表单里填了一半的内容会留着，
切走再切回来还在）。每个通道一句说明：

| 通道 | 说明 | 表单字段 |
|---|---|---|
| **Server酱** | 不用在路由器上装任何服务，填一个 SendKey 就能推到微信 | SendKey（密码框，带「显示/隐藏」）+「启用并测试」 |
| **企业微信群机器人** | 在企微群里加一个群机器人，粘 Webhook 地址 | Webhook 地址（密码框）+「启用并测试」 |
| **ClawBot** | 常驻一个微信机器人服务，直接推到微信会话 | 五步向导（见下） |
| **自定义 webhook** | Bark / ntfy / 钉钉 / 自建脚本…… | 地址 / 请求方式 / Content-Type / 请求体模板 +「启用并测试」 |
| **关闭** | 不发推送（邮件提醒不受影响） | 「关闭推送」按钮（`powerfee push off`） |

### ④ 所选通道的表单

Server酱 那格有一行指引（SendKey 在 sct.ftqq.com 微信扫码登录后获取，免费版每天有额度），
企业微信那格有获取路径（群 → 右上角 … → 群机器人 → 添加 → 复制 Webhook 地址），
自定义那格把 `{text}` `{title}` `{room}` `{balance}` … 占位符列了出来。

**密钥输入框永远不回显已保存的值**：页面只从 `push status` 拿到掩码提示（"已保存：xxx…"），
输入框是空的，填新的才会覆盖；测试失败时输入框里的内容会保留（方便改一个字再试），
成功后清空。

### ClawBot 那一格：原来的五步向导

| 状态 | 页面显示 | 你做什么 |
|---|---|---|
| ① 服务没装 | 三条安装命令（原生包 apk / opkg、Docker 过渡方案），每条带**复制**按钮 | 复制一条到 SSH 里执行（或让包管理装） |
| ② 装了但没在跑 | 原生形态：**「启动微信服务」**按钮；Docker 形态：`docker start weclawbot-api` 命令 + 复制按钮 | 点一下 / 复制执行 |
| ③ 在跑但没绑定微信 | **页面里直接显示登录二维码**（约 2 分钟过期；每 90 秒自动刷新，也有「刷新二维码」按钮） | 微信「扫一扫」 |
| ④ 绑定了但没激活 | 「打开微信，给这个 ClawBot 随便发一句话（例如『你好』）」+ 每 5 秒自动检测 | 发一条消息 |
| ⑤ 已激活但推送没配 | **「一键启用微信推送」**按钮（会读凭据 → 写 UCI → commit → 发一条测试消息） | 点一下，收到测试消息即成功 |
| ⑥ 全部就绪 | 状态摘要：BotID / 是否已激活 / 推送开关 / 最近一次推送结果；「发测试消息」「重新一键配置」按钮；「ClawBot 详情」里还有服务形态、配置来源、日志来源、API 端口、推送地址 | 平时不用管 |

已完成的步骤在进度条里打勾并折叠成一行（例如"✓ 启动服务并扫码登录：已扫码绑定 xxx@im.bot"），
只有当前这一步展开并给出「做什么 + 为什么」的一句话说明。轮询只在等扫码/等激活时快
（5 秒一次），其它状态 20 秒一次；切到别的通道就不再取二维码了。

---

## 二、前端取数走 CLI，不走 CGI（为什么）

页面需要的东西只有两类：**状态/测试/写配置**（`powerfee push ...`）和
**ClawBot 向导**（`powerfee wechat ...`）；1.2.0 起还多了**推送账号管理**
（`powerfee json push-accounts` / `powerfee push-account add|set|remove|test`，
同样是 CLI 路线）。它们同时存在于两个地方：命令行，
和 `/cgi-bin/powerfee?cmd=wechat-*` 端点。
**页面选的是 CLI 路线**（`fs.exec('/usr/bin/powerfee', ['push'|'wechat', ...])`），理由：

1. **不碰查询令牌**。CGI 要求 `token=notify.http_token`，而那个令牌是给外部机器人用的
   密钥。让浏览器去 UCI 里读一遍再拼进 URL，等于把密钥放进页面/日志/浏览器历史；
   而且 `notify.http_token` 默认是空的（此时端点一律 403），页面会先卡在
   "请先开启端点并设置令牌"——一个配置页不该以"先配置另一个东西"开场。
2. **不新增权限**。现有三个页面本来就走 `fs.exec('/usr/bin/powerfee', ...)`，
   ACL 已经授好；走 CGI 还得让浏览器去访问 8443 端口的自签证书端点。
3. **一张图更省事**。`wechat qr --json` 直接给 base64 PNG，前端用 `data:` URL 渲染，
   天然可刷新（不需要另开一个 `<img src=...>` 请求，也不用处理 404 时的图片占位）。
4. **密钥不出后端**。`push status` 只回掩码，浏览器里根本没有明文密钥可丢。

代价：**需要 python3**（`push.py` / `wechat.py` 都是 python3 写的；UCI 写入、
auth.json 读取、二维码解析都在里面）。没有 python3 时状态卡会显示一条明确的提示
（"读不到推送状态：需要 python3"），其余三个页面不受影响。ACL 只新增了一条
`/etc/init.d/weclawbot-api`（②/③ 步的「启动服务」「重启服务」按钮要用），
没有加 `/bin/apk` 之类的安装权限 —— **装包永远是要你手敲的命令**，
页面只负责把命令给你并让你一键复制。

CGI 那四个子命令仍然实现了（`wechat-status` / `wechat-qr` / `wechat-enable` /
`wechat-test`），给"外部机器人/局域网设备"用；`wechat-qr` 是**二进制 PNG**
（`Content-Type: image/png`，`Cache-Control: no-store`），拿不到可用二维码时返回
**404 + JSON 说明**（`{"error":"qr unavailable","detail":"..."}`），
不会把日志噪声混进图片里。

---

## 三、推送通道管理器（`powerfee push` / push.py）

五个通道走的是**同一条流水线**（`push.py` 里的 `apply_and_test`）：

```
写 powerfee.notify 段（uci set …）
  → uci commit powerfee
  → powerfee notify-test（真发一条，与告警推送同源：同一个 notify_send）
  → 判断结果 → JSON 回给页面/CGI
```

各通道写的键值：

| 通道 | url | method / content_type | body | token |
|---|---|---|---|---|
| Server酱 | `https://sctapi.ftqq.com/<key>.send`；key 以 `sctp` 开头时 → `https://<key 里 sctp 后的数字>.push.ft07.com/send/<key>.send` | POST / application/json | `{"title":"{title}","desp":"{text}"}` | **删掉** |
| 企业微信 | 原样写入 | POST / application/json | `{"msgtype":"text","text":{"content":"{text}"}}` | **删掉** |
| ClawBot | `http://127.0.0.1:<端口>/bots/<bot_id>/messages` | POST / application/json | `{"text":"{text}"}` | `Authorization: Bearer <api_token>` |
| 自定义 | 原样写入 | 原样写入（默认 POST / application/json） | 原样写入（缺省 `{"text":"{text}"}`） | 不动（要鉴权去设置页配） |

`timeout` 统一写 15 秒；`enabled` 一律写 1（`push off` 才置 0）；只动 `notify` 段，
不碰 `mail` / `api` / `main` 段，也不碰 `http_enabled` / `http_token`。

### 为什么测试结果不能只看 HTTP 状态码

服务端"拒绝"时不一定给非 2xx。真机实测（1.0.3 上线前）：

| 场景 | HTTP | 正文 |
|---|---|---|
| Server酱 假 SendKey | **400** | `{"message":"[AUTH]错误的Key","code":40001,"info":"错误的Key","args":[null],"scode":461}` |
| 企业微信 假 webhook | **200** | `{"errcode":93000,"errmsg":"invalid webhook url, hint: […], from ip: …"}` |

企业微信这一行是关键：只看状态码会把"地址错了"当成功（日志里甚至写"通知已推送 HTTP 200"）。
所以 1.0.3 起：

* `powerfee notify-test` 成功时也打印/记录服务端正文（`服务端返回：{"errcode":0,…}` /
  日志行 `通知已推送（原因 test）：HTTP 200：{…}`），失败时打印
  `推送失败（HTTP 400）：{"code":40001,…}`；日志里的失败行同样带正文（截 200 字节）；
* `push.py` 再解析这段正文：Server酱 看 `code`、企业微信看 `errcode`、通用看
  `ok:false` / `error` 字段，非 0 就是失败，并把服务端原文（如 `[AUTH]错误的Key`）
  作为 `message` / `error` 回给页面 —— 页面直接显示，不是只报一句"失败"；
  正文里的中文是 `\uXXXX` 转义的，会重新序列化一遍再显示。
  正文被截断（解析不出 JSON）时退回正则扫 `code`/`errcode`，原文照带；
* `push status` 的 `last_push_ok` 也按同一规则判断（HTTP 2xx + 正文里非 0 的
  `code`/`errcode` = 失败），页面上"最近一次推送"不会把这类失败显示成成功。

### 掩码规则（密钥不出后端）

| 场景 | 显示 |
|---|---|
| Server酱 URL | `https://sctapi.ftqq.com/SCT1…（已保存，34 位）.send`（占位符 `<你的SendKey>` 不是密钥，原样显示，好让你看出"还没填"） |
| 企业微信 URL | `…/webhook/send?key=693a…（已保存，36 位）`（只改名字像密钥的参数，其余原样） |
| ClawBot URL | 原样（本机回环地址，bot_id 不算密钥） |
| 密钥/令牌本身 | 只回前 4 位 + 长度（`SCT1…（已保存，34 位）`）或"已配置（已隐藏）" |

同一套掩码也用在 `powerfee status` 的"通知推送"行和推送日志里的 URL 上 ——
不然 Server酱 的 key 会随每次推送失败写进 `/etc/powerfee/powerfee.log` 和 syslog。

### 命令行

```sh
powerfee push status                       # JSON：channel/enabled/url_masked/key_hint/last_push…
powerfee push set-serverchan <SendKey>     # 写配置 + commit + 发测试
powerfee push set-wecom <webhook 地址>
powerfee push set-webhook <url> [method] [content_type] [body]
powerfee push set-clawbot [--bot <id>]     # 读 auth.json 写 url/token
powerfee push off                          # enabled=0（配置保留）
powerfee push test                         # 只发测试消息，不改配置
```

写配置后**不做回滚**：测试失败时配置照样留在 UCI 里（与 `wechat enable` 的行为一致），
页面上会显示失败原因，改完再点一次即可。`push test` 在 `enabled=0` 时直接报
"推送当前是关闭的"，不会偷偷改开关。

---

## 四、ClawBot 一键启用到底做了什么

`powerfee wechat enable`（ClawBot 面板上的「一键启用微信推送」按钮）等价于：

```sh
uci set powerfee.notify=notify
uci set powerfee.notify.enabled=1
uci set powerfee.notify.url="http://127.0.0.1:<端口>/bots/<bot_id>/messages"
uci set powerfee.notify.method=POST
uci set powerfee.notify.content_type=application/json
uci set powerfee.notify.body='{"text":"{text}"}'
uci set powerfee.notify.token_header=Authorization
uci set powerfee.notify.token="Bearer <api_token>"   # 服务端要的是 "Authorization: Bearer ..."
uci set powerfee.notify.timeout=15
uci commit powerfee
powerfee notify-test                                  # 真发一条，收到就是成功
```

（`powerfee push set-clawbot` 写的是同一组键值，区别只是它还会在测试后按通道规则
判断服务端正文；页面上 ClawBot 那一格用的仍是 `wechat enable`。）

* `<端口>`：原生形态取 `/etc/config/weclawbot-api` 里的 `option port`（取不到用 26322）；
  Docker 形态取 `docker port weclawbot-api 26322/tcp` 映射出来的宿主端口。
* `<bot_id>` / `<api_token>`：从 `auth.json` 里**自动挑一个已激活（`context_token` 非空）
  的 bot**；没有已激活的就报错并告诉你先去激活，不会瞎写一个推不出去的配置。
* **令牌不出门**：`api_token` 只用于拼 UCI，返回值里只有 `bot_id` 和 URL；
  页面上的 token 字段永远显示为空（想改就去「设置 → 通知推送」，那里本来就是密码框）。
* 先 `--dry-run` 可以只看"会写什么"（`would_set` 里令牌显示成 `<api_token>（已隐藏）`），
  不写配置、不发消息。
* 与 `mail` 段、`api` 段、`http_enabled/http_token` **完全无关**，一键启用只动
  `notify` 段的这 8 个选项。

## 五、二维码是怎么来的（以及为什么可能"没有"）

`weclawbot-api` 把登录二维码以**字符画**形式打到 stdout（半块字符 `█▀▄`，45 列，
深色底反相渲染）。`wechat.py` 的处理链路：

```
日志（原生日志文件 / logread / docker logs）
  → 去掉 docker ISO 时间戳或 syslog 前缀（Wed Oct 7 20:30:00 2026 user.notice weclawbot-api: ）
  → 找出连续的字符画行（≥12 行，只含 " ▀▄█"）
  → 半块字符还原成模块矩阵（1 行字符 = 2 行模块）
  → 自动判极性：在矩阵里找 7x7 定位图案；找不到就把矩阵反相再找（服务打印的是深色底，需要反相）
  → 按三个定位图案裁掉外框，补 4 模块静区
  → 放大 8 倍写成 1 位灰度 PNG（45+8 模块 → 360×360 像素，约 1.1 KB）
```

**"过期"是怎么判断的**：日志是追加的，登录成功后旧的二维码还留在里面。
所以 `wechat qr` 会拒绝输出"看起来已经过期"的图，两种判据（满足其一即算过期）：

* **时间**：二维码那几行的时间戳距今超过 5 分钟（`docker logs -t` 的 ISO 时间戳，
  或 logread 的本地时间戳；文件日志用文件 mtime 兜底）；
* **位置**：这块字符画后面还有 40 行以上别的日志（说明服务早就不在等扫码了）。

过期时 `qr` 返回 `ok:false` + 人话原因（"日志里最新的二维码是约 N 分钟前打印的……
要重新扫码请重启微信服务"），页面会显示"日志里只剩一张旧的二维码（服务可能已经登录过），
扫它没用"。**要拿一张真正能扫的新图**：让服务在没有有效登录的状态下重启
（原生：`/etc/init.d/weclawbot-api restart`；Docker：`docker restart weclawbot-api`），
它会重新打印 —— 页面在③步就提供了这两个按钮/命令。
调试时想强行看图可以 `wechat qr --allow-stale`。

页面刷新节奏：**每 90 秒自动重取一张**（服务约 2 分钟换一张），也可以点「刷新二维码」；
如果二维码没变（用 PNG 内容的哈希比对），页面不会闪一下重画。

---

## 六、服务形态：先探原生包，没有再看 Docker

`wechat.py` / `push.py` 里的 `Service.detect()` 顺序（配置来源与日志来源同理）：

| | 原生（推荐） | Docker（过渡方案） |
|---|---|---|
| 判定依据 | `/etc/init.d/weclawbot-api` 或 `/usr/bin/weclawbot-api` 或 `/etc/weclawbot/config/auth.json` 存在 | `docker inspect weclawbot-api` 成功 |
| 配置 | `/etc/weclawbot/config/auth.json` | `/opt/weclawbot/config/auth.json` |
| 在跑吗 | `/etc/init.d/weclawbot-api status` == running，否则 `pgrep -f weclawbot-api` | `docker inspect -f {{.State.Running}}` |
| 日志 | `/var/log/weclawbot-api.log`、`/tmp/log/weclawbot-api.log`、`/var/log/weclawbot.log`、`/etc/weclawbot/*.log` 之一 → 再退 `logread -e weclawbot` | `docker logs --timestamps --tail 400 weclawbot-api` |
| 端口 | `/etc/config/weclawbot-api` 的 `option port` | `docker port weclawbot-api 26322/tcp` |

只要原生形态存在就**不会**去看 Docker（避免"容器还在跑、原生包已装好"时两边状态打架）。
两种形态的日志前缀（docker 的 ISO 时间戳 / syslog 的 `Wed Oct 7 … user.notice tag:`）都能解析，
所以原生包把 stdout 交给 procd（进 syslog）或自己写文件都能被认出来。

> 原生包尚未落地时，用同一份日志文本在本地做过等价模拟（syslog 前缀版 + 纯文本版，
> 都能还原出同一张可解码的二维码）；真机实测那台目前是 Docker 形态。

---

## 七、排查表

| 现象 | 原因 / 怎么办 |
|---|---|
| 页面顶部红框"读不到推送状态" | 多半是没装 python3（`apk add python3` / `opkg install python3-light`），或者 powerfee 还是 1.0.2 及更早（没有 `push` 子命令）；ACL 里要有 `/usr/bin/powerfee` 的 exec |
| 「推送账号」区提示"读不到推送账号列表" | powerfee 还是 1.1.0 及更早（没有 `push-account` 子命令 / `json push-accounts`），升级到 1.2.0 即可；此时通道选择器与其它页面不受影响 |
| 页面顶部红框"无法读取微信服务状态" | 同上（ClawBot 那一格的 `wechat` 助手也要 python3） |
| 「启用并测试」返回"Server酱 返回错误：[AUTH]错误的Key（code=40001）" | SendKey 抄错了/已重置。去 sct.ftqq.com 重新复制一个（HTTP 200 但正文报错，属于服务端拒绝） |
| 「启用并测试」返回"企业微信 返回错误：invalid webhook url（errcode=93000）" | Webhook 地址复制不全，或机器人已被移除 |
| 「发测试消息」说"推送当前是关闭的" | 先在上面选通道点「启用并测试」；`push test` 不会偷偷改开关 |
| 通道选错了想换回来 | 直接点另一个通道填参数即可（`notify` 段会被覆盖写入）；只想暂停就点「关闭」 |
| 第③步一直没有二维码 | 服务刚启动要等十几秒；或者它已经登录过（日志里只有旧图）→ 点「重启服务」让它重新打印 |
| 扫了二维码没反应 | 二维码过期了（约 2 分钟）→ 点「刷新二维码」，或直接扫页面上刚刷出来的那张 |
| 第④步一直不前进 | 消息要发**给这个 ClawBot**（不是发给朋友）；发完几秒内页面会自动前进 |
| 「一键启用」报 "还没激活" | 先去第④步在微信里发一条消息 |
| 「一键启用」成功但没收到消息 | 看「最近一次推送」那行：HTTP 200 就是服务端收了；没收到多半是微信侧把该会话折叠了（ClawBot 会话不是好友聊天） |
| Server酱 说成功但微信没收到 | Server酱 侧的问题（免费版额度用完、未关注公众号、被折叠）→ 去 sct.ftqq.com 的消息记录里看 |
| `wechat-qr` 端点返回 404 | 正常：拿不到**可用**二维码时就是 404 + JSON 原因（旧图不给，免得白扫） |
| 查询端点全部 403 | `notify.http_enabled=1` 之外还要设 `notify.http_token`；留空等于端点关闭 |
| 日志里 URL 显示成 `SCT1…（已隐藏）` | 正常：1.0.3 起推送日志/status 里的 URL 一律掩码，免得把 Server酱 key 写进日志 |

