# wechat-clawbot-bridge —— 微信 ClawBot 电量查询桥接

在路由器上常驻一个 **python3 小进程**（标准库，无第三方依赖），把微信 ClawBot（iLink）收到的
消息解析出来；命中关键词就调用 `powerfee` 查询，并把结果通过 ClawBot 的 HTTP API 回给绑定用户。

即：**在微信里发「电量」，收到当前余额。**

> 当前现场（2026-10-07 迁移后）：微信服务已从 Docker 容器改为**原生包**
> `/usr/bin/weclawbot-api`（procd 服务 `/etc/init.d/weclawbot-api`，监听 26322，
> 配置 `/etc/weclawbot/config/auth.json`，stdout/stderr 进 syslog）。
> 容器 `weclawbot-api` 已停用（`Exited (0)`、`restart=no`，保留做回滚）。
> 桥接默认跟随**原生服务的 syslog**（`SOURCE=logread`），不再依赖 docker。

## 数据流

```
微信 ClawBot 聊天窗口
   │  你发「电量」
   ▼
weclawbot-api（原生服务；只发不收，但把收到的消息打到 stdout → procd → syslog）
   │  syslog 行：  Wed Oct  7 21:04:55 2026 daemon.info sh[9104]: [Bot: <bot_id> | Message from <user>]: 电量
   ▼
powerfee-chat（本桥接）
   │  logread -f -e 'Message from' → 正则解析 → 关键词匹配 → 节流 / 去重
   ▼
powerfee json brief / status（本机命令）
   │
   ▼
POST http://127.0.0.1:26322/bots/<bot_id>/messages   {"text": "..."}
   ▼
微信 ClawBot 聊天窗口（你收到余额）
```

## 文件清单

| 仓库文件 | 路由器位置 | 权限 |
|---|---|---|
| `powerfee-chat.py` | `/usr/bin/powerfee-chat` | 755 |
| `powerfee-chat.init` | `/etc/init.d/powerfee-chat` | 755 |
| `powerfee-chat.conf.example` | `/etc/powerfee-chat.conf` | **600** |

`api_token` 不在仓库和配置文件里，默认从原生服务的配置
`/etc/weclawbot/config/auth.json`（结构 `{"bots": {"<bot_id>": {"api_token": "..."}}}`）读取；
也可在 conf 里写 `API_TOKEN=`。

## 安装

### 方式一：装 OpenWrt 包（推荐）

本桥接已打成 OpenWrt 包 **`powerfee-chat`**（定义在 `openwrt/powerfee-chat/`，
依赖 `python3` + `powerfee`，纯脚本、`all` 架构）：装完三个文件按上表的路径与权限
一次就位（`/etc/powerfee-chat.conf` 登记为 conffile、权限 600），不用手工铺。

```sh
# OpenWrt 25.x 及以上（apk）：未签名包要 --allow-untrusted；
# 用签名包（先装公钥 signing/powerfee-signing.pem 到 /etc/apk/keys/）则不需要
apk add --allow-untrusted /tmp/powerfee-chat_1.1_all.apk
# apk add /tmp/powerfee-chat-1.1-r1.apk

# OpenWrt 24.10 及以下（opkg）
opkg install /tmp/powerfee-chat_1.1-1_all.ipk
```

装完**不会自动启用/启动**（和主包 `powerfee` 的行为一致）。按下面的第 2~4 步
核对配置后再 `enable && start` 即可。

### 方式二：手工铺文件（不装包，任何 OpenWrt 版本都能用）

```sh
# 1. 上传三个文件（dropbear 无 sftp，用 cat > 的方式）
cat > /usr/bin/powerfee-chat.new && chmod 755 /usr/bin/powerfee-chat.new && mv -f /usr/bin/powerfee-chat.new /usr/bin/powerfee-chat
cat > /etc/init.d/powerfee-chat.new && chmod 755 /etc/init.d/powerfee-chat.new && mv -f /etc/init.d/powerfee-chat.new /etc/init.d/powerfee-chat
cat > /etc/powerfee-chat.conf.new && chmod 600 /etc/powerfee-chat.conf.new && mv -f /etc/powerfee-chat.conf.new /etc/powerfee-chat.conf
```

（两种方式装完之后的步骤相同：）

```sh
# 2. 检查配置（token 只显示长度；source command 就是实际会跑的命令）
python3 /usr/bin/powerfee-chat --check-config

# 3. 离线自测（假 API + 假 powerfee，不打扰微信）
python3 /usr/bin/powerfee-chat --selftest

# 4. 开机自启 + 启动
/etc/init.d/powerfee-chat enable
/etc/init.d/powerfee-chat start
/etc/init.d/powerfee-chat status        # running
logread -e powerfee-chat | tail         # 日志
```

## 指令（微信里发）

| 指令 | 效果 |
|---|---|
| `电量` / `电费` / `查电费` / `余额` / `剩余` / `balance`（可带 `/` 前缀，大小写不敏感） | 单行摘要（`powerfee json brief` 的 `text` 原样转发） |
| `电量 详情` / `/电费 全部` | 多行详情（房间 / 余额 / 档位 / 阈值 / 上次查询，≤6 行） |
| `曲线` / `用电` / `用量` / `趋势` | 最近 7 天每日用电量**文本曲线**（`powerfee json usage --days 7` 的 `text`：宽 32 列、微信里不折行；末尾带合计/日均与数据来源） |
| `帮助` / `help` | 指令列表 |

关键词表可在配置里改。**同一用户、同一类指令 3 秒内只回一次**（防刷屏）。
曲线关键词先于「电量」判断（「用电量」这类消息回曲线；纯「电量」仍是单行摘要）；
微信通道只回文本曲线、不发 PNG（通道不保证支持图片，要图用邮件报告）。

## 配置（`/etc/powerfee-chat.conf`，改完 `reload`）

| 键 | 默认 | 说明 |
|---|---|---|
| `SOURCE` | `logread` | 消息来源：`logread`（原生）/ `logfile` / `docker`（旧容器，回退） |
| `LOGREAD_FILTER` | `Message from` | 传给 `logread -f -e` 的过滤模式（正则，匹配整行）。**见下方说明** |
| `LOGFILE` | `/var/log/weclawbot.log` | `SOURCE=logfile` 时跟踪的文件（`tail -F -n 0`） |
| `CONTAINER` / `DOCKER_LOG_ARGS` | `weclawbot-api` / `--since 1s` | `SOURCE=docker` 时用 |
| `API_BASE` | `http://127.0.0.1:26322` | ClawBot HTTP API（原生/容器同端口） |
| `BOT_ID` | 空 | 每日推送用；回复时用日志行里的 bot_id |
| `AUTH_JSON` | `/etc/weclawbot/config/auth.json` | api_token 来源 |
| `API_TOKEN` | 空 | 直接写死 token（留空则用 AUTH_JSON） |
| `POWERFEE_BIN` | `/usr/bin/powerfee` | powerfee 可执行文件 |
| `QUERY_KEYWORDS` / `DETAIL_KEYWORDS` / `HELP_KEYWORDS` | 见示例 | 关键词表（逗号/空格分隔） |
| `USAGE_KEYWORDS` / `USAGE_DAYS` | `曲线,用电,用量,趋势,usage,chart` / `7` | 每日用电量曲线的关键词与天数（调 `powerfee json usage --days N`） |
| `THROTTLE_SECONDS` | `3` | 同一用户同一类指令的回复间隔 |
| `DEDUP_SECONDS` | `5` | 无时间戳重复行的去重窗口（有时间戳的行用长窗口精确去重） |
| `IGNORE_FROM` | 空 | 额外忽略的 from（`from == bot 自己` 总是忽略） |
| `IGNORE_TAGS` | `powerfee-chat` | 忽略这些 syslog tag 的行（防自回环，见下） |
| `DAILY_PUSH_TIME` | 空 | 每日定时推送余额，`HH:MM`（本地时间），留空=关闭 |
| `LOG_FILE` | 空 | 额外日志文件；留空=仅 syslog（`logger -t powerfee-chat`） |

### 为什么 `LOGREAD_FILTER` 不是 tag

原生服务的 init 用 `procd_set_param command /bin/sh -c "cd /etc/weclawbot && exec /usr/bin/weclawbot-api ..."`，
**procd 捕获 stdout 时按命令名打 tag，所以二进制自己的输出在 syslog 里是 `sh[<pid>]:`**
（实测：`Wed Oct  7 20:58:05 2026 daemon.info sh[9104]: [Bot: ...] Started listening for messages...`），
而不是 `weclawbot-api`。因此 `logread -e weclawbot-api` **抓不到**真实消息行；
默认改为按消息内容过滤 `-e 'Message from'`（`logread -e` 匹配整行文本），
这样无论 tag 是什么都能命中。

### 消息来源切换

- 原生服务（现状）：`SOURCE=logread` + `LOGREAD_FILTER=Message from`
- 服务把 stdout 落文件：`SOURCE=logfile` + `LOGFILE=/var/log/weclawbot.log`
- 旧容器回滚：`SOURCE=docker` + `CONTAINER=weclawbot-api`（容器需先 `docker start`）

三种来源共用同一套正则与处理管线（`process_line()`）。切换后
`python3 /usr/bin/powerfee-chat --check-config` 可以看到实际会执行的命令。
断流（logd 重启、日志轮转、`tail`/`logread` 退出）都会自动重连（退避 1→30 秒；
连接稳定 ≥30 秒才重置退避）。

## 诊断命令

```sh
python3 /usr/bin/powerfee-chat --check-config        # 生效配置（token 打码）
python3 /usr/bin/powerfee-chat --selftest            # 49 项离线断言
python3 /usr/bin/powerfee-chat --send-text '测试'    # 只发一条（诊断微信发送链路）
python3 /usr/bin/powerfee-chat --once-line '<日志行>' # 把一条日志行喂进真实管线
```

## 卸载

```sh
/etc/init.d/powerfee-chat stop
/etc/init.d/powerfee-chat disable
rm -f /etc/init.d/powerfee-chat /usr/bin/powerfee-chat /etc/powerfee-chat.conf
```

## 已验证（2026-10-07 真机，原生服务形态）

| 项 | 结果 |
|---|---|
| 日志源 | 实际用 `SOURCE=logread`（`/sbin/logread -f -e 'Message from'`），容器已停用、桥接不再调用 docker |
| `--selftest`（路由器上） | **41/41 PASS**（含 `sh[pid]` tag 行解析、自家 tag 忽略、节流、去重、噪声、三种来源命令） |
| 稳定性 | 切换后连续运行无重连（45s 窗口内 syslog 无「日志源断开」） |
| 真实日志形态 | 原生服务启动横幅实测：`daemon.info sh[9104]: Loaded 1 bots.` / `[Bot: ...] Started listening for messages...`（tag 是 `sh`，见上） |
| 注入验证（tag `sh`，与真实形态一致） | 桥接日志 `处理: ... | Wed Oct  7 21:08:50 2026 user.notice sh: [Bot: ... | Message from ...]: 电量`；非关键词行不回复 |
| 防自回环 | 桥接自己的 `处理: ...` 行也含 "Message from" 会被 logread 抓到，但按 `IGNORE_TAGS=powerfee-chat` 丢弃；实测处理计数 2→2→3→3，无二次触发 |
| 包安装（2026-10-07 23:05，`powerfee-chat_1.0_all.apk`） | `apk add` 一次装好三个文件：`/usr/bin/powerfee-chat`（= 仓库源，sha 3c693688…）、`/etc/init.d/powerfee-chat`、`/etc/powerfee-chat.conf`（0600，登记为 conffile）；装完**不自动启动**（postinst 打印配置提示并 disable+stop）；`enable && start` 后 `status` = running，ps 里 `python3 /usr/bin/powerfee-chat --daemon` + `logread -f -e Message from` 两个进程都在 |
| 注入验证（23:06，包安装后） | `logger -t sh '[Bot: … | Message from <假用户>]: 电量'` → `处理: replied:brief`；非关键词行不触发；同一用户 3 秒内两次「电量」只处理一次（节流）；`restart` 后再注入「电量 详情」→ `处理: replied:detail`，防自回环计数稳定 |
| 发送链路 | 2026-10-07 21:08 曾被原生服务侧阻塞（`HTTP 500 prepare failed`，需 owner 在微信里先发一条消息刷新会话）；**23:06 复测已恢复**：`POST /bots/<id>/messages` 返回 `{"code":200,"message":"OK"}`，桥接日志 `发送成功 bot=… resp={…}`（真机注入消息的回复实际送达绑定会话） |

## 已知限制 / 脆弱点（如实）

- **发送依赖上游会话就绪**：iLink 要求"先给 ClawBot 发过消息拿到 context_token"才能主动发。
  迁移到原生服务后，从容器复制的会话凭据可能已失效，`--send-text` 直接调用也返回
  `prepare failed`。**需要 owner 在微信里给 ClawBot 发一条消息**（刷新会话）后重试；
  若仍失败，则是原生服务侧需要重新登录/扫码，与本桥接无关。
  （2026-10-07 21:08 实测被限流阻塞，23:06 复测已恢复——见上表「发送链路」。）
- **日志解析是"搭车"方案**：依赖上游把收到的消息按
  `[Bot: <bot_id> | Message from <user>]: <text>` 固定格式打到 stdout。
  上游改日志格式 / 降日志级别 / 改 tag 规则，桥接会**静默失效**。
  缓解：`logread | grep 'Message from'` 能看到原始行；`logread -e powerfee-chat` 能看到每条处理记录。
- **只支持单用户**：bot 只绑定一个微信号，回复直接发到绑定会话（无法指定收件人）。
- **服务停机期间的消息不会补答**：`logread -f` 只推新行，停机窗口内的消息丢失。
- **不要把容器 json 日志当输入直接追加**（仅回滚到 `SOURCE=docker` 时相关）：
  会让 dockerd 日志读写偏移失配，`docker logs` 报 `unexpected error: EOF`（重启容器可恢复）。
  桥接本身只读跟随，不碰日志文件。
- **关键词是宽松匹配**：中文关键词按子串匹配（"余额"出现在任何句子里都会触发），
  英文按词边界匹配（`balance` 不会命中 `balances`）。误报可接受，但知道即可。
- **节流粒度**是"用户 + 指令类别"（简要/详情/帮助）：同一用户 3 秒内先发「电量」再发「余额」只回一条。
- **微信 iLink 是灰度通道**：token 可能过期，需要重新扫码；这是上游风险，与本桥接无关。
