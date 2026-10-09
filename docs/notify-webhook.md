# notify webhook 通用说明

`config notify 'notify'` 是 powerfee 的**通用 HTTP 推送出口**：
任何能收 HTTP 请求的平台（企业微信机器人、钉钉、飞书、Bark、ntfy、Server 酱、自建脚本……）
都能接，不需要写代码。

微信 / QQ 的具体接法见 [`notify-wechat-qq.md`](notify-wechat-qq.md)。

## 配置项

| 选项 | 含义 | 默认 |
|---|---|---|
| `enabled` | 总开关 | `0` |
| `url` | 接收端完整地址 | 空 |
| `method` | `POST` / `GET` | `POST` |
| `content_type` | POST 的 Content-Type | `application/json` |
| `body` | 请求体模板（`GET` 时忽略） | `{"text":"{text}"}` |
| `token` | 鉴权凭据 | 空 |
| `token_header` | 把 token 放在哪个 header（空 = 不放） | 空 |
| `timeout` | 单次请求超时（秒） | `10` |

> **1.2.0 起**，上面这些字段还可以写进**命名推送账号**（`config push_account`）里，
> 房间用 `notify_account` 引用 —— 一台路由器可以有多套推送配置，见下文
> 「多个推送账号 + 按房间绑定」。

## body 模板占位符

| 占位符 | 内容 |
|---|---|
| `{text}` | **单行摘要**（含 emoji，可直接嵌进 JSON / URL 参数） |
| `{title}` | 标题（如「⚠️ 宿舍电费不足」） |
| `{room}` | 房间名 |
| `{balance}` | 当前余额（数字） |
| `{unit}` | 余额单位（度 / kWh / 元…） |
| `{level}` | 档位：`low` / `warn` / `ok` / `unknown` |
| `{reason}` | 触发原因（low / warn / recovered / stale / test…） |
| `{daily}` | 日均用量 |
| `{days_left}` | 预计可用天数 |
| `{time}` | 查询时间 |
| `{device}` | 设备名（如 `OpenWrt（宿舍电量哨兵 v1.0.3）`） |

## 示例

> 下面这些都是「手工改配置」的写法。LuCI 的 **服务 → 宿舍电量哨兵 → 微信推送** 页
> 顶部可以直接选通道（Server酱 / 企业微信群机器人 / ClawBot / 自定义 / 关闭），
> 填好参数点「启用并测试」就会写好这些配置并发一条测试消息；命令行等价物是
> `powerfee push set-serverchan <key>` / `set-wecom <url>` / `set-webhook <url> [method] [ctype] [body]`。
> 只有页面/命令覆盖不到的场景（比如要加自定义请求头）才需要照下面手改。

**Server酱（推到自己微信，最省事）** —— POST，正文是 `{"title":…,"desp":…}`：

```sh
uci set powerfee.notify.enabled='1'
uci set powerfee.notify.url='https://sctapi.ftqq.com/<你的SendKey>.send'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='application/json'
uci set 'powerfee.notify.body={"title":"{title}","desp":"{text}"}'
uci -q delete powerfee.notify.token
uci -q delete powerfee.notify.token_header
uci commit powerfee
powerfee notify-test
```

* SendKey 在 <https://sct.ftqq.com> 微信扫码登录后获取（免费版每天有额度，个人收告警够用）。
* 以 `sctp` 开头的 key 是 **Server酱³**，地址形态不同：
  `https://<key 里 sctp 后的数字>.push.ft07.com/send/<key>.send`
  （例：`sctp1234txxxx…` → `https://1234.push.ft07.com/send/sctp1234txxxx….send`）。
  `powerfee push set-serverchan <key>` 会自己识别这两种形态。
* **key 填错时服务端是 HTTP 200 + 正文报错**：`{"code":40001,"message":"[AUTH]错误的Key"}`。
  `powerfee notify-test` 会把这段正文一起打出来，LuCI 页面也会原样显示。
* Server酱用 URL 里的 key 鉴权，**不要**再配 `token`（否则会多带一个无用的鉴权头）。

**Bark（iPhone）** —— GET，摘要放路径：

```sh
uci set powerfee.notify.enabled='1'
uci set powerfee.notify.url='https://api.day.app/<your_key>/{title}/{text}'
uci set powerfee.notify.method='GET'
uci set powerfee.notify.token_header=''
uci set powerfee.notify.token=''
uci commit powerfee
```

**ntfy.sh** —— POST，正文放 body：

```sh
uci set powerfee.notify.url='https://ntfy.sh/<topic>'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='text/plain'
uci set 'powerfee.notify.body={text}'
uci commit powerfee
```

**企业微信机器人**（群设置 → 群机器人 → 添加 → 复制 Webhook 地址）：

```sh
uci set powerfee.notify.url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>'
uci set powerfee.notify.method='POST'
uci set powerfee.notify.content_type='application/json'
uci set 'powerfee.notify.body={"msgtype":"text","text":{"content":"{text}"}}'
uci -q delete powerfee.notify.token
uci -q delete powerfee.notify.token_header
uci commit powerfee
powerfee notify-test
```

企业微信同样可能「HTTP 200 + 正文报错」：`{"errcode":93000,"errmsg":"invalid webhook url"}`
（地址复制错/机器人被删）。`powerfee push set-wecom <url>` 与 LuCI 页面都会把这段原文显示出来。
注意群机器人只能在企业微信的群里用，个人微信群不行。

**自建脚本（带 token 的 JSON POST）**：

```sh
uci set powerfee.notify.url='http://192.168.1.10:8080/powerfee'
uci set powerfee.notify.token_header='X-Auth-Token'
uci set powerfee.notify.token='<secret>'
uci set 'powerfee.notify.body={"text":"{text}","room":"{room}","balance":{balance},"level":"{level}","reason":"{reason}"}'
uci commit powerfee
```

## 多个推送账号 + 按房间绑定（1.2.0）

「A 房间推到 A 的机器人、B 房间推到 B 的」有两种做法：

- **1.1.0 起**：在 `config room` 段里写 `notify_url` / `notify_token`（每个房间一条地址）；
- **1.2.0 起（推荐）**：把整套推送配置写成**命名推送账号**（`config push_account`），
  多个房间引用同一个账号 —— 地址、令牌、模板只维护一份，房间侧只写一个账号名。

```sh
# ① 建账号：1号楼 A101 用 Server酱，2号楼 B202 用宿舍群的企业微信机器人
powerfee push-account add sc_room1
powerfee push-account set sc_room1 channel serverchan
powerfee push-account set sc_room1 url 'https://sctapi.ftqq.com/<你的SendKey>.send'
powerfee push-account set sc_room1 method POST
powerfee push-account set sc_room1 content_type application/json
powerfee push-account set sc_room1 body '{"title":"{title}","desp":"{text}"}'

powerfee push-account add wecom_group
powerfee push-account set wecom_group channel wecom
powerfee push-account set wecom_group url 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>'
powerfee push-account set wecom_group body '{"msgtype":"text","text":{"content":"{text}"}}'

powerfee push-account list            # 账号清单（地址 / 令牌只回掩码）
powerfee push-account test sc_room1   # 用这个账号发一条测试消息
powerfee push-account remove wecom_group

# ② 房间绑定（留空 '' 改回默认 notify 段）
powerfee room-set room1 notify_account sc_room1
powerfee room-set room2 notify_account wecom_group
```

等价的 UCI 写法（`/etc/config/powerfee`）：

```
config push_account 'sc_room1'
	option enabled '1'
	option channel 'serverchan'    # serverchan|wecom|clawbot|custom；留空继承 notify
	option url 'https://sctapi.ftqq.com/<你的SendKey>.send'
	option method 'POST'
	option content_type 'application/json'
	option body '{"title":"{title}","desp":"{text}"}'
	option token ''
	option token_header ''
	option timeout ''
	option bot_id ''               # channel=clawbot 时的 bot id
```

（房间侧：`config room 'room1'` 里写 `option notify_account 'sc_room1'`。）

**取值优先级（推送）**：

1. **基底** = 房间的 `notify_account`（空 → `notify` 段）；账号里留空的字段再继承 `notify` 段的
   同名字段（`channel` / `url` / `method` / `content_type` / `body` / `token` / `token_header` / `timeout`）；
2. 房间级的 `notify_url` / `notify_token` **覆盖基底的同名字段** —— 1.1.0 写的每房间地址继续有效；
3. `notify_enabled=0` 时这个房间不推（留空继承 `notify.enabled`）。

没有任何 `config push_account` 段、房间也没写引用时，行为与 1.1.0 **完全一致**。

LuCI：**服务 → 宿舍电量哨兵 → 微信推送** 页的「推送账号」区（新增 / 编辑 / 删除 / 发测试消息），
**宿舍管理** 页的房间编辑弹窗里选「推送账号」（含「跟随默认」）。
地址与令牌一律只显示掩码，编辑时留空表示不修改。

## 注意事项

- **每个房间可以有自己的推送地址**（1.1.0 起）：在 `config room` 段里写 `notify_url`
  （以及可选的 `notify_token` / `notify_enabled`），留空则继承 `notify` 段。
  这样「A 房间推到 A 的群机器人、B 房间推到 B 的」可以共存；`powerfee push set-*`
  只改全局 `notify` 段，不动各房间自己的地址（见 README「多个房间」）。
  **1.2.0 起**还可以给房间绑定命名推送账号（`notify_account`），见上一节。
- **JSON 里不能有裸换行**：`{text}` 是单行摘要，可以直接嵌；要推多行文案请用 `\n` 转义。
- **`{balance}` 是数字**，想当字符串用要自己加引号（`"{balance}"`）。
- **`GET` + 中文**：放 URL 路径/参数里的 `{text}` 需要接收端能处理 percent-encoding，
  不确定就改用 `POST`。
- **失败不阻塞监控**：推送失败只会写日志（`powerfee log` / `logread -e powerfee`），
  不会影响余额监控与告警判定；同一档位有冷却，不会刷屏。
- **凭据尽量放 `token` 字段**，不要写进 `url`；`/etc/config/powerfee` 已是 600 权限。
  像 Server酱 / 企业微信这种「key 只能放在 URL 里」的通道躲不掉，所以 1.0.3 起
  `powerfee status`、推送日志与 `powerfee push status` 里的 URL **都是掩码**
  （`…/SCT1…（已隐藏）.send`、`?key=693a…（已隐藏）`），不会把密钥写进日志或终端；
  LuCI 页面也只会显示掩码，要换密钥就在输入框里填新的。
