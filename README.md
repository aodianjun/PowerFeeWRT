# 宿舍电量哨兵 · OpenWrt 版

在 OpenWrt 路由器上 **7×24 监控宿舍电费余额**，余额不足时**发邮件**提醒。

- 不绑定任何学校：**接口地址、请求方式、返回字段全部靠配置适配**（见[适配你的学校](#适配你的学校)）
- 常驻路由器，电脑关机、手机休眠时依然在盯着电表
- 邮件走 `python3` 或 `msmtp`，纯文本 + HTML 双格式，中文不乱码
- 提供 `.apk`（OpenWrt 25.x 及以上）与 `.ipk`（OpenWrt 24.10 及以下）两种安装包，以及 **LuCI 网页界面**

```
$ powerfee status
宿舍电量哨兵 · OpenWrt 版 v1.2.4
--------------------------------
房间        ：1号楼 A101（东区）  [编号 1001]
当前余额    ：71.00 度
状态        ：✅ 充足（阈值 20 度 / 预警区 20 × 2）
日均用量    ：约 3.20 度/天
预计可用    ：约 22.2 天
上次查询    ：2026-10-07 15:23:11
上次提醒    ：2026-10-07 15:23:11（recovered）
累计查询    ：4 次
查询时段    ：全天（active_hours 未设置）
查询间隔    ：30 分钟
邮件提醒    ：已启用 → you@qq.com（smtp.qq.com:465 ssl，投递方式 python3）
服务状态    ：running
```

> 上面是示例输出，房间号是随便取的，你的房间要用 `powerfee set-room <房间号>` 指定。
> 配置了多个房间时，这里会按房间逐个列出余额（见[多个房间](#多个房间不同收件人)）。

## 为什么值得单独做个路由器版

同类工具的常见短板是"设备不在线就没人看着"：桌面端要求电脑开着，手机端会被省电策略
掐后台（系统限制最小 15 分钟，Doze 还会继续推迟）。

路由器 7×24 插着电、连着网，是宿舍里唯一"永远在线"的设备。它可以在你电脑关机、
手机睡觉的时候继续盯着电表，并且用电邮把提醒送到任何地方。

## 功能

- **余额监控**：按配置的间隔（默认 30 分钟）查询电费接口，记录历史采样
- **查询时段**（可选）：只在指定时段内请求接口（如 `07:00-23:00`、跨天的 `22:00-06:00`、多段），减少夜间请求；窗口外不发请求也不会误报监控失效（见[只在特定时段查询](#只在特定时段查询减少夜间请求)）
- **多个房间**（可选）：一台路由器同时监控几个房间，每个房间有自己的收件邮箱与推送通道，判定/冷却/历史互相独立（见[多个房间](#多个房间不同收件人)）
- **一次添加多个房间**（1.2.1）：宿舍管理页的搜索结果可勾选（点行切换、表头全选本页、已在监控的自动跳过），一次加一批；后端只请求学校接口**一次**就匹配全部目标，逐房间报告，部分失败不整体失败（见[在界面里一次添加多个房间](#在界面里一次添加多个房间多选)）
- **多个发件邮箱账号**（可选，1.2.0）：发件方也能多套 —— 每个命名账号一套 SMTP（服务器/端口/加密/登录/密码/发件人），房间用 `mail_account` 指定用哪套，留空用默认 `mail` 段（见[多个发件邮箱账号](#多个发件邮箱账号)）
- **多个推送账号**（可选，1.2.0）：命名推送账号（Server酱 / 企业微信 / ClawBot / 自定义 webhook），房间用 `notify_account` 绑定，不同房间可推到不同微信 / QQ（见[多个推送账号](#多个推送账号)）
- **低电量邮件预警**：余额低于阈值时发邮件，正文含余额、阈值、日均用量、预计可用天数
- **预警区提醒**（可选）：进入 `阈值 × 倍数`（默认 2 倍）的预警区时提前提醒
- **充值恢复提醒**：余额回到阈值以上后提醒一次
- **监控失效告警**：连续 N 小时（默认 6）取不到数据也发邮件 —— 避免"以为在看，其实早瞎了"
- **日均用量估算**：用最近 72 小时采样估算每天用多少、还能用几天
- **每日用电量曲线**（1.2.0）：逐日用电量统计 + 文本 / PNG / HTML 三种曲线图 ——
  命令行可看、邮件里带内联图、微信机器人可回文本图（见[每日用电量与曲线](#每日用电量与曲线)）
- **定时用电报告**（1.2.0）：每天到点（`main.report_time`）发一份「今日用量 + 最近 N 天曲线」，
  邮件带内联曲线图、推送可带文本曲线
- **纯文本 + HTML 双格式邮件**，中文标题不乱码
- **通知推送（webhook）**：同一事件还能推到一个 HTTP 地址，可接微信 / QQ 机器人等（见[推到微信 / QQ](#推到微信--qq通知推送-webhook)）
- **HTTP 查询端点**：局域网设备/机器人可用 `https://<路由器IP>:8443/cgi-bin/powerfee?token=...&cmd=brief` 读余额
- **命令行工具**：查状态、搜房间、发测试邮件/测试通知、看历史、跑自检；另有 `powerfee json` 机器接口
- **LuCI 网页界面**（可选装）：**状态页 / 用电量页 / 宿舍管理页 / 设置页 / 微信推送页** —— 余额与最近 7 天用量一览、逐日曲线与数据表、多选批量添加房间、全部配置项图形化、推送通道选择器与 ClawBot 二维码向导（见[网页界面](#网页界面luci)）
- **UCI 配置 + procd 常驻**，改配置不用重启服务

## 安装

### 安装包与架构支持（先读这一小节）

本项目同时提供**两种包格式**，选哪个只看你的 OpenWrt 版本：

| 包格式 | 适用于 | 产物 |
|---|---|---|
| `.apk` | OpenWrt **25.x 及以上**（apk-tools 3） | `apk/powerfee_1.2.4_all.apk`、`apk/luci-app-powerfee_1.2.4_all.apk`、`apk/powerfee-chat_1.1_all.apk` |
| `.ipk` | OpenWrt **24.10 及以下**（opkg 系） | `ipk/powerfee_1.2.4-1_all.ipk`、`ipk/luci-app-powerfee_1.2.4-1_all.ipk`、`ipk/powerfee-chat_1.1-1_all.ipk` |

> **`powerfee-chat`（微信查询桥接包，可选）**：装到路由器上后，在微信里给 ClawBot
> 发「电量」就能收到当前余额（盯着 weclawbot-api 的日志 → 调 `powerfee json brief`
> → 回消息）。依赖 `powerfee` + `python3`，同样纯脚本、`all` 架构、装完不自动启动；
> 用法与配置见 [integrations/wechat-clawbot-bridge/README.md](integrations/wechat-clawbot-bridge/README.md)。

不确定自己是哪种，在路由器上跑一下就知道：

```sh
command -v apk     # 有输出 → 25.x+，用 .apk
command -v opkg    # 有输出 → 24.10 及以下，用 .ipk
```

**为什么一个 `all` 包就够了 —— 不用挑架构，也不存在"装错架构"**

这些包**全部是脚本**：ash 主程序、python3 投递助手/查询桥接、uhttpd CGI、LuCI 的 JS/JSON ——
**没有任何架构相关二进制**。所以包里写的是 `Architecture: all`（apk 里显示 `noarch`），
**同一个包在 armv7a（`arm_cortex-a7` 等）、armv8a/aarch64（`aarch64_cortex-a53` 等）、
x86（i386）、x86_64 以及其它架构上都通用**，不需要为每个架构各打一份。

> 确实需要把 `Architecture` 写成具体值（例如私有源按架构分目录、按架构做校验）时，
> 可以用 `--arch` 参数生成，默认仍是 `all`：
>
> ```sh
> sh openwrt/build-ipk.sh --arch aarch64_cortex-a53
> # → ipk/powerfee_1.2.4-1_aarch64_cortex-a53.ipk（只改元数据字段与文件名，包内容不变）
> ```

**LuCI 界面的版本要求（重要）**

`luci-app-powerfee` 是 **LuCI JS（客户端渲染）** 界面（`www/luci-static/resources/view/powerfee/*.js`
+ `usr/share/luci/menu.d/` + `usr/share/rpcd/acl.d/`）。这套机制 **OpenWrt 19.07 起才有、21.02 起完善**，
所以**建议 OpenWrt 21.02 及以上**；更老的（18.06 及以前）是 Lua 版 LuCI，本界面不适用 ——
那些机器上只装主包 `powerfee` 也能用（命令行、邮件提醒与查询端点都不依赖 LuCI）。

包依赖：主包 `powerfee` → `jsonfilter, curl, ca-bundle`；界面包 → `powerfee, luci-base`。
两种格式装完都**不会自动启用/启动服务**，要自己开（见下面「装完之后」）。

### 包的签名（可选：让 apk 正常校验，不再需要 `--allow-untrusted`）

`apk/`、`ipk/` 里的产物是**未签名**的构建输出，所以下面「方式一」本地安装要用 `--allow-untrusted`。
本仓库同时提供**自签名**方案（`signing/`）：公钥 + **已签名**的 .apk + 已签名的自建仓库索引。
把公钥装进设备后，安装签名包（或从自建源安装）走正常校验、不需要 `--allow-untrusted`：

```sh
# 一次性：信任本项目公钥（文件名任意，apk v3 按密钥身份匹配）
cp signing/powerfee-signing.pem /etc/apk/keys/

# 之后正常安装（无需 --allow-untrusted）
apk add signing/repo/powerfee-1.2.4-r1.apk
apk add signing/repo/luci-app-powerfee-1.2.4-r1.apk

# 多设备/长期用：把 signing/repo/ 发布到 HTTP，加一行自建源即可
echo 'http://<你的服务器>/powerfee/packages.adb' > /etc/apk/repositories.d/powerfee.list
apk update && apk add powerfee
```

> **官方签名拿不到**：OpenWrt 官方包/索引是 buildbot 的私钥签的（公钥随固件装在 `/etc/apk/keys/`），
> 第三方无法让 OpenWrt 代签，除非包被官方 feed 收录。自建包的正解就是自签名。
> 机制细节（apk-tools 3 实测）、作者侧完整命令、自建签名仓库用法与真机验证记录：
> **[docs/signing.md](docs/signing.md)**。

### 方式一：装 .apk 包（OpenWrt 25.x 及以上）

```sh
# 把 apk/ 下的包传到路由器（powerfee-chat 是微信查询桥接，可选装）
scp -O apk/powerfee_1.2.4_all.apk apk/luci-app-powerfee_1.2.4_all.apk apk/powerfee-chat_1.1_all.apk root@192.168.1.1:/tmp/

# 在路由器上安装（apk/ 里的产物未签名，所以用 --allow-untrusted；
# 想免掉这个参数：用上面「包的签名」里的签名包 + 公钥）
ssh root@192.168.1.1
apk add --allow-untrusted /tmp/powerfee_1.2.4_all.apk
apk add --allow-untrusted /tmp/luci-app-powerfee_1.2.4_all.apk   # 想要网页界面再装
apk add --allow-untrusted /tmp/powerfee-chat_1.1_all.apk         # 想要微信查电量再装
```

装完包不会自动启动服务，需要你自己开（见下面的「启动服务」）。

### 方式二：装 .ipk 包（OpenWrt 24.10 及以下，opkg 系）

```sh
# 把 ipk/ 下的包传到路由器（powerfee-chat 是微信查询桥接，可选装）
scp -O ipk/powerfee_1.2.4-1_all.ipk ipk/luci-app-powerfee_1.2.4-1_all.ipk ipk/powerfee-chat_1.1-1_all.ipk root@192.168.1.1:/tmp/

# 在路由器上安装（本地 .ipk 不需要签名，也不用配源）
ssh root@192.168.1.1
opkg install /tmp/powerfee_1.2.4-1_all.ipk
opkg install /tmp/luci-app-powerfee_1.2.4-1_all.ipk   # 想要网页界面再装
opkg install /tmp/powerfee-chat_1.1-1_all.ipk         # 想要微信查电量再装

opkg files powerfee              # 看装了哪些文件
opkg remove powerfee luci-app-powerfee   # 卸载（/etc/powerfee 与 /etc/config/powerfee 会保留）
```

`/etc/config/powerfee` 在包里登记为 conffile：升级时你的配置不会被覆盖，
卸载时**改过的**配置会留一个备份（`/etc/config/powerfee-opkg`）。

### 方式三：跑安装脚本（不装包，直接把文件放上去）

```sh
# 把整个目录拷到路由器
# OpenSSH 9.0+ 的 scp 默认走 SFTP，而 dropbear 只有 scp 协议，所以要加 -O
scp -O -r PowerFeeWRT root@192.168.1.1:/tmp/
# 如果 scp 不通，用 tar 走管道也行（只需要远端有个 shell）：
# tar czf - PowerFeeWRT | ssh root@192.168.1.1 "cd /tmp && tar xzf -"

ssh root@192.168.1.1 sh /tmp/PowerFeeWRT/install.sh
```

安装脚本做的事：把文件放到 `/usr/bin/powerfee`、`/usr/lib/powerfee/mail.py`、
`/etc/init.d/powerfee`，写入默认配置 `/etc/config/powerfee`（已存在则保留，不会覆盖你的邮箱授权码），
把 `/etc/powerfee` 加进 `/etc/sysupgrade.conf`（固件升级后保留状态），并设置开机自启。

### 装完之后

```sh
# 1) 配接口（见「适配你的学校」）
# 2) 选宿舍
powerfee rooms A1          # 搜索房间
powerfee set-room A101    # 设置要监控的房间
# 3) 配邮箱（见下一节）后发一封测试邮件
powerfee test-mail
# 4) 启动服务并设开机自启
/etc/init.d/powerfee enable && /etc/init.d/powerfee start
```

## 适配你的学校

本工具**不含任何学校的地址或字段名**，全部靠配置描述。只要你的学校电费接口
返回「每个房间一条记录」的 JSON，就能用。

### 第一步：找到接口

打开学校的电费查询页面，按 F12 打开开发者工具 → Network，刷新页面，
找到那个返回一堆房间余额的请求，记下它的：

- **URL**（连查询参数一起，通常长这样 `https://xxx.edu.cn/api/rooms?token=yyy`）
- **请求方式**（GET 或 POST）与**请求体**（POST 常见一个固定的串，例如 `PARAM=XXXX`）

> 这类接口很多是免登录的：不带 Cookie 也能拿到数据（本工具就靠这一点）。
> 但也有学校需要登录态，那就得自己带上 Cookie —— 本工具不处理登录流程。

### 第二步：看返回结构，填映射

假设接口返回：

```json
{
  "code": 0,
  "data": {
    "rooms": [
      { "id": "1234", "name": "A101", "building": "1号楼",
        "campus": "东区", "balance": "71.01" }
    ]
  }
}
```

对应配置就是：

```sh
uci set powerfee.api=api                 # 先建 section（直接编辑 /etc/config/powerfee 也行）
uci set 'powerfee.api.url=https://xxx.edu.cn/api/rooms?token=yyy'
uci set powerfee.api.method='POST'       # GET 就写 GET
uci set powerfee.api.body='PARAM=XXXX'       # 学校要求的固定请求体
uci set powerfee.api.rooms_path='@.data.rooms[*]'  # 房间数组在哪一层
uci set powerfee.api.ok_path='@.code'              # 成功标志（可选）
uci set powerfee.api.ok_value='0'
uci set powerfee.api.msg_path='@.message'          # 失败时的错误信息（可选）
uci set powerfee.api.field_id='id'                 # 房间唯一编号
uci set powerfee.api.field_name='name'             # 房间名（你搜索用的）
uci set powerfee.api.field_building='building'
uci set powerfee.api.field_campus='campus'
uci set powerfee.api.field_balance='balance'
uci set powerfee.api.unit='度'
uci commit powerfee

powerfee rooms A1        # 能列出房间就说明配对了
```

⚠️ **URL 里的 `&` 一定要加引号**（`uci set 'powerfee.api.url=...'`），否则 shell 会把它当后台符号。

**`rooms_path` 与 `field_*` 用的是 `jsonfilter` 语法**（OpenWrt 自带的小工具）：

| 写法 | 含义 |
|---|---|
| `@.obj[*]` | 顶层 `obj` 数组里的每个元素（**房间数组就是这一层**） |
| `@.data.rooms[*]` | 嵌套路径同理 |
| `@.ret` | 取 `ret` 字段的值（用于成功标志） |

`field_*` 填的是**字段名**，不是路径 —— 因为每行记录都是扁平的。
如果你的学校没有楼栋/校区概念，把那两项留空即可（界面上就只显示房间名）。

调试用：

```sh
powerfee json rooms            # 看解析出来的房间列表（JSON）
powerfee log 20                # 看最近的查询日志与报错
curl -s '你的接口地址' | head  # 看接口原始返回（GET 的情况）
```

## 配置邮箱

配置在 `/etc/config/powerfee` 的 `mail` 段（`uci` 命令和直接编辑文件都行）。
**`password` 填的是邮箱「授权码」，不是登录密码** —— QQ / 163 / Gmail 都要求用授权码。

```sh
uci set powerfee.mail.enabled=1
uci set powerfee.mail.host='smtp.qq.com'
uci set powerfee.mail.port='465'
uci set powerfee.mail.security='ssl'
uci set powerfee.mail.user='你的QQ号@qq.com'
uci set powerfee.mail.password='邮箱授权码'      # QQ 邮箱：设置 → 账户 → 开启 SMTP 服务后拿到
uci set powerfee.mail.to='收件邮箱@example.com'  # 多个用逗号分隔
uci commit powerfee
powerfee test-mail
```

常见邮箱参数：

| 邮箱 | host | port | security |
|---|---|---|---|
| QQ 邮箱 | smtp.qq.com | 465 | ssl |
| 163 邮箱 | smtp.163.com | 465 | ssl |
| Gmail | smtp.gmail.com | 465 | ssl |
| 自建 / 企业邮 | 你的服务器 | 587 | starttls |

发件人默认用 `mail.user`；想换个显示名改 `mail.from` 和 `mail.from_name`。
`/etc/config/powerfee` 里存着授权码，安装脚本/包已把它设成 600 权限，别提交到公开仓库。
1.2.0 起还能配**多套发件账号**、按房间指定（见[多个发件邮箱账号](#多个发件邮箱账号)）。

## 只在特定时段查询（减少夜间请求）

夜里没人看余额，但路由器还在按间隔请求学校接口。`main.active_hours` 可以限制查询时段：

```sh
uci set powerfee.main.active_hours='07:00-23:00'   # 只在 07:00 ~ 23:00 查
uci commit powerfee
```

- 格式 `HH:MM-HH:MM`，**多段用空格分隔**：`07:00-12:00 14:00-23:00`
- **跨天**：起 > 止 表示跨越午夜，`22:00-06:00` = 22:00 到次日 06:00
- 边界是「含起点、不含终点」：`07:00-23:00` 表示 07:00 开始查、23:00 起停止查
- 起止相同（`00:00-00:00`）表示全天；**留空 = 全天查询**（默认，与旧版完全一致）
- 格式写错（如 `7:00-23`）会按「全天」处理 —— 宁可多查，不能让监控因为一个笔误静默失效；
  `powerfee status` 与 `powerfee json status` 里会标出「格式无效」

窗口外会发生什么：

- **不发任何请求**，守护进程照常活着，到点自动恢复（窗口外每分钟醒一次看时间）
- **不会误报「监控失效」**：`stale_hours` 的判定基准会把窗口外的时长扣掉
  （进入窗口外时记下暂停起点，回到窗口内时把这段时长加到 `last_ok_at` 上），
  所以设了 `07:00-23:00` + `stale_hours=6` 也不会每天早上收到一封「已 N 小时未取到数据」
- **冷却照常流逝**：回到窗口内时，若余额仍是低电量且冷却已过，会立刻补一条提醒（这是有意的）
- 排障用 `powerfee check --force` 无视时段强制查一次；LuCI 状态页的「立即查询」按钮
  在窗口外会被跳过并提示原因（界面按钮走的是普通 check，不是 --force）

`powerfee status` 会显示当前时段与「现在是否在时段内」；`powerfee json status` 里多了
`active_hours` / `in_active_window` / `active_hours_valid` 三个字段。

## 每日用电量与曲线

余额曲线只能看出「花了多少」，看不出「哪天花得多」。1.2.0 起多了一份**每日用电量**：
逐日统计 + 曲线图（文本 / PNG / HTML 三种形态），能进邮件、能进微信回复、也能在命令行看。

### 数据从哪来（两条来源，自动选）

| 来源 | 什么时候用 | 说明 |
|---|---|---|
| **逐日接口**（推荐） | 配了 `api.daily_url` | 直接问学校接口要「某房间某月每天用了多少」。历史月**缓存**到 `/etc/powerfee/usage.<房间>.<YYYY-MM>.json`（历史不会变，第二次起不重复请求）；当月每次重取（约 3 KB） |
| **本地采样估算**（兜底） | 没配 `daily_url`，或接口取不到，或这个房间在接口里没有逐日数据 | 用 `history.<房间>.csv` 里的余额采样估算：每段采样的用量归给「后一点所在的那天」（跨午夜的间隔归午夜后那天），**余额上升（充值）不参与累加**，采样间隔 > 2×间隔的那天标为不完整 |

所以**什么都不配也能用**：只要服务跑过一段时间（有历史采样），`powerfee usage` 就能画出曲线；
配上逐日接口则数据更准、还能看任意历史月份。

### 配置逐日接口（`config api` 段）

和 `api.url` 同一风格，填好接口地址与字段映射即可（下面是通用示例，换成你学校的）：

```sh
uci set 'powerfee.api.daily_url=https://你的学校/user/powerfee/getDailyDetails'
uci set 'powerfee.api.daily_body=roomNum={room}&lastDate={month}&type=2&pageNum={page}&pageSize={page_size}&implType=你的接口参数&token=&from='
uci commit powerfee
```

模板占位符：`{room}` 房间号、`{month}` `YYYY-MM`、`{page}` 页码、`{page_size}` 每页天数
（默认 31，一个月一页）。返回结构映射同样靠配置（`daily_path` / `daily_ok_path` / `daily_date_field`
/ `daily_used_field` / `daily_total_field` / `daily_unit_field`，默认值按常见形态给好，
见[配置项](#配置项)里的 `config api` 表）。请求头纪律与余额接口完全一致：
只带 `Content-Type`，**不发 `Origin`**（有些学校的 WAF 见到它直接 403），
解析失败时同样会用 `api.dns_servers` 里的公共 DNS 解析后直连。

> **月末缺日那件事（已知坑，已处理）**：这类接口有个常见毛病 —— 每个**历史月**的最后一天
> 不返回（跨几个月都一样，永远 N-1 天）。本工具会用下个月 1 日的数据把它恢复出来：
>
> ```
> 用量(M月最后一天) = 累计(M+1月1日) − 累计(M月最后返回日) − 用量(M+1月1日)
> ```
>
> （用的是接口里的「终身累计」字段，它是单调递增的。）**下个月的数据取不到时就把那天标成缺值，
> 图上断开一截 —— 不猜、不瞎补**。当月的数据本来就是「到昨天为止」，不参与这个恢复。

### 命令

```sh
powerfee usage                       # 最近 7 天曲线 + 一行摘要（默认当前房间）
powerfee usage --days 30             # 最近 30 天
powerfee usage --month 2026-09       # 整个 9 月（历史月走缓存）
powerfee usage --date 2026-10-05     # 只看某一天用了多少
powerfee usage --png /tmp/usage.png  # 顺便把 PNG 曲线写到文件（可发图片）
powerfee usage room2 --days 14       # 多房间时指定房间（段名或编号）

powerfee report                      # 立刻发一份报告（邮件 + 推送，每个房间各一份）
powerfee report room1 --days 14      # 只给某个房间发，曲线用 14 天

powerfee json usage --days 7         # 给机器人用：JSON（text 文本曲线 + days 逐日数组 + summary）
powerfee json usage --png            # 多回一个 png_base64（只在显式要时才给）
powerfee json usage --png /tmp/u.png # 机器口也能顺带写文件（--png 后跟路径即写）
```

`usage` 的输出长这样（文本曲线 + 摘要 + 数据来源）：

```
每日用电量（度）
17 ┤      ██
   │   ▅▅ ██
   │▄▄██▄▄██
11 ┼▄▄██▄▄██
    10-01 10-07

合计 98.00 度 ｜ 日均 14.00 度 ｜ 最高 17.00（10-07） ｜ 最低 11.00（10-01） ｜ 7 天
数据来源：学校接口逐日数据
```

### 定时报告（每天到点自动发）

```sh
uci set powerfee.main.report_time='21:00'   # 每天 21:00 发；留空 = 不发（默认）
uci set powerfee.main.report_days='7'       # 曲线天数（默认 7）
uci commit powerfee
```

到点后**每个启用的房间**各发一份「今日用量 + 最近 N 天曲线」，走该房间自己的收件人、
发件账号与推送账号（与告警同一套账号体系）。两个细节：

- **与查询时段不冲突**：报告时间落在 `active_hours` 之外也能发 —— 曲线来自逐日接口
  （与查询时段无关），「今日用量」用当天已有的本地采样。不会因为窗口外不查就发空报告。
- **与告警互不干扰**：报告是独立事件，不改告警档位、不影响冷却与去重；报告发不出去
  也只记日志（当天最多重试 3 次），不会影响正常的余额告警。

### 曲线进邮件 / 进微信

- **邮件**：报告邮件与**余额告警邮件**都会带上曲线。HTML 部分用内联 PNG
  （`multipart/related` + `Content-ID`，在邮件客户端里直接显示，**不是附件**；约 3 KB），
  生成不了 PNG 时退回纯 HTML 表格图。测试邮件、监控失效邮件不带图（别把每封都撑大）。
- **微信 / QQ 推送**：报告推送的单行摘要之外，请求体模板里写 `{chart}` 就会带上**文本曲线**
  （换行转义成 JSON 的 `\n`，只适合 JSON 请求体）。例：
  `option body '{"text":"{text}\n{chart}"}'`
- **微信桥接（powerfee-chat）**：在微信里发「**曲线**」「用电」「用量」就会回最近 7 天的
  文本曲线（宽 32 列，微信里不折行；见 [integrations/wechat-clawbot-bridge](integrations/wechat-clawbot-bridge/README.md)）。

## 多个房间（不同收件人）

默认只监控一个房间（`main.room_num`）。想让**不同房间的提醒发给不同邮箱 / 不同机器人**，
就加 `config room` 段 —— 用命令加最省事：

> 每房间的**收件人**（`mail_to`）与**推送目标**（`notify_url` / `notify_token` / `notify_enabled`）
> 在 **1.1.0** 就已经支持；**1.2.0 起还能给每个房间指定不同的「发件账号」（`mail_account`）与
> 「推送账号」（`notify_account`）** —— 前者换的是发件人那一套 SMTP 配置，后者把房间绑到一套
> 命名推送配置上。两者留空都回落到默认的 `mail` / `notify` 段，详见
> [多个发件邮箱账号](#多个发件邮箱账号)与[多个推送账号](#多个推送账号)。

```sh
powerfee rooms A101          # 先搜到房间
powerfee room-add A101 --mail-to a101@example.com --notify-url 'https://example.com/hook/a'
powerfee room-add 2002 --id room2 --label '2号楼 B202（西区）' --threshold 50 \
    --mail-to b202@example.com --notify-url 'https://example.com/hook/b'

# 批量：一次给多个编号（接口只请求一次，不是每个房间拉一遍）
powerfee room-add 1001 2002 3003 --mail-to dorm@example.com

powerfee room-list           # 看已配置的房间（余额 / 收件人 / 推送通道）
powerfee room-set room1 mail_to 'a101@example.com,c101@example.com'
powerfee room-remove room2   # 删除（连同它的状态与历史）
```

批量添加的约定：

- **只请求接口一次**：全校房间列表本来就一次返回，多个编号在同一份响应里匹配
  （按编号匹配，编号匹配不到再按房间名匹配），不会变成「每个房间拉一次接口」。
- **逐房间报告**：每行一个结果（`成功：` / `跳过：` / `失败：`），最后一行汇总
  `汇总：成功 X 个，跳过 Y 个，失败 Z 个（共 N 个）`；**全部成功退出码 0，有失败非 0**。
  部分失败不整体失败 —— 能加的都加上，加不上的单独报出来。
- **去重**：已经在 `config room` 里的房间（按 `num` 编号比）会报「已在监控」并跳过，
  不会重复建段；同一批里重复给同一个编号也只建一个段。
- **段名**：默认每个房间各取第一个空闲的 `room1` / `room2`…；用 `--id 前缀` 时，
  第一个房间用前缀本身，之后的自动递增（`room2` → `room3`、`dorm` → `dorm2`），
  **绝不覆盖已有段**。选项（`--label` / `--mail-to` / `--notify-url` / `--notify-token` /
  `--threshold`）对整批生效，需要不同收件人的房间分开加或用 `powerfee room-set` 再改。

等价的 UCI 写法（`/etc/config/powerfee`）：

```
config room 'room1'
	option enabled '1'
	option num '1001'          # 接口里的房间编号（必填）
	option label ''            # 展示名；留空用「楼栋 房间」
	option campus ''
	option building ''
	option room ''
	option threshold ''        # 留空继承 main.threshold
	option mail_to ''          # 留空继承 mail.to；可写多个，逗号分隔
	option mail_account ''     # 用哪个命名发件账号（1.2.0）；留空 = 默认 mail 段
	option notify_url ''       # 留空继承 notify.url；也可写自己的
	option notify_token ''     # 同上，留空继承
	option notify_account ''   # 用哪个命名推送账号（1.2.0）；留空 = 默认 notify 段
	option notify_enabled ''   # 留空继承 notify.enabled；1/0 可单独开关这个房间的推送
```

要点：

- **向后兼容**：没有任何 `config room` 段时行为与旧版完全一致（用 `main.room_num/...`，
  状态与历史仍在 `/etc/powerfee/state`、`history.csv`）。老用户升级后不配也能照常跑。
- **升级不丢历史**：添加第一个房间时，如果它的 `num` 等于原来的 `main.room_num`，
  主程序会把老的 `state` / `history.csv` 继承给它（`state.room1` / `history.room1.csv`），
  老文件保留不删；`last_alert_at` 一起继承，所以不会因此重复发提醒。
  LuCI「宿舍管理」页里点「把当前房间加入多房间管理」就会自动做这件事。
- **一次请求、多房间分发**：接口本来就一次返回全校房间，多房间**不会**增加请求数
  （每轮查询仍然只请求一次），也不受「一次只能查一个房间」的限制。
- **各自独立**：每个房间独立判定档位、独立冷却、独立历史与日均估算；提醒只发给
  它自己的收件人 / 推送通道（`mail_to` / `notify_url` / `notify_token` 留空则继承全局），
  1.2.0 起还可以各自指定发件账号与推送账号（`mail_account` / `notify_account`，留空同样继承默认段）。
- **段必须是命名段**（`config room 'room1'`）。用 `uci add powerfee room` 建出来的**匿名段
  不生效**——匿名段的实际段名是 uci 内部生成的，按 `@room[0]` 取不到值。主程序会检测到
  并打印警告（不是静默忽略），按提示改用 `powerfee room-add` 或手写成命名段即可。
- `threshold` 可按房间覆盖；`warn_ratio` / `cooldown` / `stale_hours` 目前是全局的。
- 不带房间参数的命令（`status` / `json brief` / `history` / `test-mail` / `notify-test`）
  作用于**第一个启用的房间**；指定房间用 `powerfee history room2`、`powerfee test-mail room2`。
- LuCI：**服务 → 宿舍电量哨兵 → 宿舍管理** 页可以增删改房间并单独设置收件人 / 发件账号 /
  推送账号 / 通道 / 阈值；状态页会按房间列出余额、送达方式与账号。

### 在界面里一次添加多个房间（多选）

**宿舍管理** 页的搜索结果（搜索框、按校区 / 楼栋浏览、「加载全部房间」都会出这张表）
每行前面有一个复选框：

1. **勾选**：点复选框或**点整行**都是「选中 / 取消选中」（一次可以勾多个；
   表头复选框是**全选本页**，只作用于当前显示的行 —— 表格按 `limit` 截断时
   会写明「已显示 N / 共 M」，页面上方还有「全选本页 N 个」按钮）；
2. **已经在监控里的房间**会带一个灰色的「已监控」标记，复选框与按钮都是禁用的
   （避免白勾；真要重加先在上面「已监控的房间」列表里删掉）；
3. 下方「添加为监控房间」面板会写**已选 N 个**（列出前几个，超过 3 个显示「等 N 个」），
   按钮是 **「添加选中的 N 个」**（N=0 时禁用）；
4. 点按钮后界面只调用主程序**一次**（`json room-add --batch …`，接口只请求一次），
   然后**逐房间报告结果**：
   - 成功的弹绿色提示，列出房间名与它落的 `config room` 段名；
   - 已在监控的弹黄色提示「已在监控，已跳过 N 个」（主程序按编号去重，不会重复建段）；
   - 没找到 / 写配置失败的弹红色提示，逐个写明原因（部分失败不影响成功的那些）；
5. 结果提示之后列表会自动刷新，刚加上的房间马上变成「已监控」，可以直接继续勾下一批。

多房间下 `powerfee status` 长这样：

```
宿舍电量哨兵 · OpenWrt 版 v1.2.4
--------------------------------
监控房间    ：2 个（多房间模式）
  房间 #1  ：1号楼 A101（东区）  [编号 1001]  ← room1
      余额  ：71.01 度（✅ 充足，阈值 20 度）
      查询  ：2026-10-07 15:23:11，累计 4 次
      送达  ：邮件 → a101@example.com ｜ 推送 → POST https://example.com/hook/a
  房间 #2  ：2号楼 B202（西区）  [编号 2002]  ← room2
      余额  ：45.00 度（🔔 接近阈值，阈值 50 度）
      查询  ：2026-10-07 15:23:11，累计 4 次
      送达  ：邮件 → b202@example.com ｜ 推送 → POST https://example.com/hook/b
查询时段    ：全天（active_hours 未设置）
查询间隔    ：30 分钟
邮件提醒    ：已启用 → global@example.com（smtp.qq.com:465 ssl，投递方式 python3）
服务状态    ：running
```

## 多个发件邮箱账号

1.2.0 起，**发件方也能有多套**：`config mail_account` 是**命名发件账号**（只描述发件方 ——
SMTP 服务器、登录、密码、发件人，**不含收件人**），房间用 `mail_account` 引用它；
留空则用默认的 `mail` 段。收件人始终由房间的 `mail_to` 决定（留空继承 `mail.to`）。

典型场景：1号楼 A101 的提醒从 `qq_main` 发，2号楼 B202 的提醒从公司邮箱 `corp_mail` 发 ——
收件人各看各的，发件人也不一样。

```sh
# ① 建两个发件账号（写进 /etc/config/powerfee）
powerfee mail-account add qq_main
powerfee mail-account set qq_main host smtp.example.com   # 换成你邮箱的服务商，如 smtp.qq.com
powerfee mail-account set qq_main port 465
powerfee mail-account set qq_main security ssl
powerfee mail-account set qq_main user you@example.com
powerfee mail-account set qq_main password '邮箱授权码'
powerfee mail-account set qq_main from_name '宿舍电量哨兵（1号楼）'

powerfee mail-account add corp_mail
powerfee mail-account set corp_mail host smtp.example.com
powerfee mail-account set corp_mail port 587
powerfee mail-account set corp_mail security starttls
powerfee mail-account set corp_mail user alerts@example.com
powerfee mail-account set corp_mail password '企业邮授权码'

powerfee mail-account list           # 列出账号（密码只回掩码与「是否已设置」）
powerfee mail-account test qq_main   # 用这个账号发一封测试邮件
powerfee mail-account remove corp_mail

# ② 给房间指定发件账号
powerfee room-set room1 mail_account qq_main
powerfee room-set room2 mail_account corp_mail
powerfee room-set room1 mail_account ''    # 改回默认（mail 段）
```

等价的 UCI 写法（`/etc/config/powerfee`）：

```
config mail_account 'qq_main'
	option enabled '1'
	option transport ''      # auto|python3|msmtp；留空继承 mail.transport
	option host ''           # 留空继承 mail.host
	option port ''
	option security ''
	option user ''
	option password ''
	option from ''
	option from_name ''
	option tls_verify ''     # 留空继承 mail.tls_verify
```

（房间侧：`config room 'room1'` 里写 `option mail_account 'qq_main'`。）

**取值优先级（邮件）**：

1. **发件方** = 房间的 `mail_account`（空 → `mail` 段）；账号里留空的字段再继承 `mail` 段的同名字段
   （`transport` / `host` / `port` / `security` / `user` / `password` / `from` / `from_name` / `tls_verify`）；
2. **收件人** = 房间的 `mail_to`（空 → `mail.to`）；
3. `mail.enabled=0` 时整条邮件通道关闭 —— 多账号不会绕过这个总开关。

没有任何 `config mail_account` 段、房间也没写引用时，行为与 1.1.0 **完全一致**。

LuCI：**服务 → 宿舍电量哨兵 → 设置** 页底部的「邮箱账号（额外）」区可以**新增 / 编辑 / 删除 /
发测试邮件**；**宿舍管理** 页的房间编辑弹窗里有「发件账号」下拉（含「跟随默认」选项）。
密码只显示「已设置」掩码，编辑时留空表示不修改。

## 多个推送账号

1.2.0 起，**推送目标也能多套**：`config push_account` 是**命名推送账号**，
房间用 `notify_account` 引用它；留空则用默认的 `notify` 段。这样「A 房间推到自己的
Server酱、B 房间推到宿舍群的企业微信机器人」可以共存。

```sh
# ① 建两个推送账号（写进 /etc/config/powerfee）
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

powerfee push-account list            # 列出账号（地址 / 令牌只回掩码）
powerfee push-account test sc_room1   # 用这个账号发一条测试消息
powerfee push-account remove wecom_group

# ② 给房间绑定推送账号
powerfee room-set room1 notify_account sc_room1
powerfee room-set room2 notify_account wecom_group
```

等价的 UCI 写法（`/etc/config/powerfee`）：

```
config push_account 'sc_room1'
	option enabled '1'
	option channel ''        # serverchan|wecom|clawbot|custom；留空继承 notify
	option url ''            # 留空继承 notify.url；channel=clawbot 且 url 空时用本地 ClawBot API
	option method ''
	option content_type ''
	option body ''
	option token ''
	option token_header ''
	option timeout ''
	option bot_id ''         # channel=clawbot 时的 bot id
```

（房间侧：`config room 'room1'` 里写 `option notify_account 'sc_room1'`。）

**取值优先级（推送）**：

1. **基底** = 房间的 `notify_account`（空 → `notify` 段）；账号里留空的字段再继承 `notify` 段的
   同名字段（`channel` / `url` / `method` / `content_type` / `body` / `token` / `token_header` / `timeout`）；
2. 房间级的 `notify_url` / `notify_token` **覆盖基底的同名字段**（单房间微调仍然有效）；
3. `notify_enabled=0` 时这个房间不推（留空继承 `notify.enabled`）。

没有任何 `config push_account` 段、房间也没写引用时，行为与 1.1.0 **完全一致**。

LuCI：**服务 → 宿舍电量哨兵 → 微信推送** 页新增的「推送账号」区可以**新增 / 编辑 / 删除 /
发测试消息**（通道选 Server酱 / 企业微信群机器人 / ClawBot / 自定义 webhook，或留空跟随默认）；
**宿舍管理** 页的房间编辑弹窗里有「推送账号」下拉（含「跟随默认」选项）。
地址与令牌一律只显示掩码，编辑时留空表示不修改。

## 推到微信 / QQ（通知推送 webhook）

除了发邮件，`powerfee` 还能把告警**推到一个 HTTP 地址**（webhook）—— 这就能接到
微信 / QQ 机器人、企业微信、钉钉、Bark、ntfy 等任何支持 HTTP 的通道。

配置在 `/etc/config/powerfee` 的 `notify` 段，与 `mail` 段**互相独立**
（可以只开推送不开邮件，也可以两个都开）：

```sh
uci set powerfee.notify.enabled=1
uci set 'powerfee.notify.url=https://你的机器人地址/xxx'   # URL 里的 & 记得加引号
uci set powerfee.notify.method='POST'             # POST | GET
uci set powerfee.notify.content_type='application/json'
uci set powerfee.notify.body='{"text":"{text}"}'  # 请求体模板，占位符见下表
uci set powerfee.notify.token='你的令牌'           # 可选：放在请求头里
uci set powerfee.notify.token_header='Authorization'
uci commit powerfee
powerfee notify-test                              # 立刻推一条测试通知并打印结果
```

`notify-test` 失败会明确告诉你原因（并写进日志，`powerfee log` 可查），不会静默。
告警推送与邮件**完全同源**：低电量、重复提醒、预警区、充值恢复、监控失效与恢复、
`check --force`、测试，每个事件都会推一条 —— 只看 `notify.enabled` 这个开关。
1.2.0 起还能建**多个命名推送账号**、按房间绑定（见[多个推送账号](#多个推送账号)）。

### 在 LuCI 里选推送通道（推荐）

**服务 → 宿舍电量哨兵 → 微信推送** 页面顶部有一个**推送通道选择器**，五个通道任选其一：

| 通道 | 要填什么 | 写进 `notify` 段的东西 |
|---|---|---|
| **Server酱** | 一个 SendKey | `url=https://sctapi.ftqq.com/<key>.send`（key 以 `sctp` 开头时走 Server酱³ 的 `https://<key 里 sctp 后的数字>.push.ft07.com/send/<key>.send`）、`method=POST`、`content_type=application/json`、`body={"title":"{title}","desp":"{text}"}`，并清掉 `token` / `token_header` |
| **企业微信群机器人** | Webhook 地址 | `url=<地址>`、`method=POST`、`body={"msgtype":"text","text":{"content":"{text}"}}`，并清掉 `token` / `token_header` |
| **ClawBot** | 不用填（走页面里的五步向导） | `url=http://127.0.0.1:<端口>/bots/<bot_id>/messages` + `token=Bearer <api_token>` |
| **自定义 webhook** | url / method / content_type / body | 原样写入这四个字段（要加鉴权请求头就去「设置 → 通知推送」配 `token` / `token_header`） |
| **关闭** | — | `notify.enabled=0`（通道配置保留，随时可以开回来） |

每个通道的按钮都是「**启用并测试**」：写配置 → `uci commit powerfee` → 立刻发一条测试消息，
结果直接显示在页面上。**服务端原文会一起显示**，而且不只看 HTTP 状态码 ——
Server酱 的 SendKey 填错时返回 `{"code":40001,"message":"[AUTH]错误的Key"}`（实测 HTTP 400），
企业微信的地址不对时返回 `{"errcode":93000,"errmsg":"invalid webhook url"}`（实测 **HTTP 200**），
两种都会把服务端原话显示出来，而不是只报一句"失败"。
已保存的密钥不会回显（页面只拿到掩码提示，要换就填新的）。

命令行等价物（页面走的就是它）：

```sh
powerfee push status                       # JSON：当前通道 / 开关 / 掩码地址 / 最近一次推送
powerfee push set-serverchan <SendKey>     # 写配置 + commit + 发测试
powerfee push set-wecom <webhook 地址>
powerfee push set-webhook <url> [method] [content_type] [body]
powerfee push set-clawbot                  # 切回 ClawBot（读 auth.json 写 url/token）
powerfee push off                          # 关掉推送（enabled=0，配置保留）
powerfee push test                         # 只发一条测试消息，不改配置
```

SendKey 在 [sct.ftqq.com](https://sct.ftqq.com) 微信扫码登录后获取（免费版每天有额度，个人收告警够用）；
企业微信的 Webhook 地址在「群 → 右上角 … → 群机器人 → 添加机器人」里复制。
各平台的照抄配置见 [`docs/notify-webhook.md`](docs/notify-webhook.md)。

### 请求体模板与占位符

| 占位符 | 含义 | 示例 |
|---|---|---|
| `{text}` | **单行**摘要，设计上可直接嵌进 JSON 字符串（不含引号/换行/反斜杠） | `⚠️ 宿舍电费不足：1号楼 A101 仅剩 8.22 度（阈值 20 度，约可用 2.6 天）` |
| `{title}` | 与邮件相同的标题 | `⚠️ 宿舍电费不足：1号楼 A101 仅剩 8.22 度` |
| `{room}` `{balance}` `{unit}` `{level}` `{reason}` `{daily}` `{days_left}` | 各字段原值（缺失为空串） | `A101` / `8.22` / `度` / `low` / `ok->low` |
| `{time}` | 本地时间 | `2026-10-07 15:23:11` |
| `{device}` | 设备名 | `OpenWrt` |
| `{chart}` | **多行文本曲线**（1.2.0）：只有 `powerfee report` / 定时报告推送时才有值；换行转义成 JSON 的 `\n`，只适合 JSON 请求体 | `每日用电量（度）\n17 ┤ ██\n…` |

替换时值会做 JSON 安全转义（引号 / 反斜杠 / 换行 / 制表符），中文原样透传；
未知占位符原样保留。有 `token` 时加请求头 `"$token_header: $token"`（值原样使用，不加 `Bearer` 前缀）。

`method=GET` 时渲染结果作为 query string 拼在 `url` 后面，并做 URL 百分号编码
（空格/中文/引号等编码成 `%XX`，`=` 与 `&` 保留作参数分隔符）——不编码的话 curl
遇到空格会直接判 URL 非法。接收端按常规 URL 解码即可拿回原文。

各平台的具体配方（企业微信机器人、QQ 机器人、Bark、ntfy…）见 [`docs/`](docs/)。

### 微信（ClawBot）：在 LuCI 里点几下就好

推到**微信**用的是官方 ClawBot（iLink）通道 + 本机的 `weclawbot-api` 服务
（原生 OpenWrt 包，或过渡期的 Docker 容器）。这条链路步骤多（装服务 → 扫码 →
在微信里发一条消息激活 → 写推送配置），所以界面上专门做了一个**微信推送**页
把五步串起来，每屏只显示当前该做的那一件事（它就是上面通道选择器里的 **ClawBot** 那一格）：

**服务 → 宿舍电量哨兵 → 微信推送** → 选 **ClawBot**：没装就显示安装命令（带复制按钮）；
装好没扫码就直接在页面里显示二维码（约 2 分钟过期，自动刷新，也能手动刷新）；扫完让你
在微信里发一句话激活；最后点**「一键启用微信推送」** —— 它会自己读服务凭据、写 UCI、
commit 并发一条测试消息。全流程不用敲命令、不用复制令牌。

命令行等价物（LuCI 页走的就是它）：

```sh
powerfee wechat status    # JSON：装没装 / 在跑没跑 / 绑定没绑定 / 激活没激活 / 推送配置对不对
powerfee wechat qr --json # 服务日志里的登录二维码（base64 PNG；也可 --out 存成文件）
powerfee wechat enable    # 一键写推送配置 + commit + 发测试消息
powerfee wechat test      # 只发一条测试消息
```

细节、原理与边界（二维码为什么可能"过期"、原生包与 Docker 两种形态怎么探测）见
[`docs/luci-wechat-page.md`](docs/luci-wechat-page.md)；ClawBot 的原理与手工配置见
[`docs/notify-wechat-qq.md`](docs/notify-wechat-qq.md)。**需要 python3**（二维码解析与
凭据读取都靠它）。

### 查询端点（给机器人读余额）

`notify` 段里还能开一个**只读 HTTP 查询端点**（uhttpd 的 CGI，走 8443 端口）：

```sh
uci set powerfee.notify.http_enabled=1
uci set powerfee.notify.http_token='一串你自己定的令牌'
uci commit powerfee
curl -k 'https://192.168.1.1:8443/cgi-bin/powerfee?token=一串你自己定的令牌&cmd=brief'
# {"ok":true,"text":"✅ 宿舍电量当前状态：1号楼 A101 剩余 71.00 度（阈值 20 度，约可用 22.2 天）",...}
```

接口形状：

```
GET/POST /cgi-bin/powerfee?token=<http_token>&cmd=<子命令>[&kw=<关键词>][&limit=<n>][&n=<行数>]
```

| cmd | 说明 |
|---|---|
| `status` | 与 `powerfee json status` 相同（余额/档位/日均/上次提醒/查询时段/rooms 数组…） |
| `brief` | 单行摘要（与 webhook 的 `{text}` 同一实现），适合机器人直接转发 |
| `groups` | 校区 → 楼栋 两级清单 |
| `rooms` | 房间列表，支持 `kw`（关键词）与 `limit`（最多条数） |
| `history` | 最近采样点，`n` 指定条数 |
| `log` | 最近运行日志，`n` 指定行数 |
| `check` | **真的查一次**（可能触发告警推送/邮件）—— 有意为之，方便机器人主动刷新；配置了 `active_hours` 且当前在窗口外时会跳过（返回 `skipped=true`） |
| `room-list` | 已配置（正在监控）的房间，含余额/档位/收件人/掩码后的推送地址 |
| `usage` | 逐日用电量（默认房间、最近 `main.report_days` 天）：`days` 逐日数组 + `summary` + 文本曲线；**不含 `png_base64`**（曲线图只有网页版取）。只读，不接受参数 |
| `wechat-status` | 微信服务状态 JSON（装没装 / 在跑没跑 / 绑定激活 / 推送配置），同 `powerfee wechat status` |
| `wechat-qr` | **二维码 PNG**（`Content-Type: image/png`，不是 JSON）；拿不到可用二维码时 `404` + JSON 说明 |
| `wechat-enable` | **写操作**：一键写 notify 配置 + commit + 发测试消息（返回里不含令牌） |
| `wechat-test` | 只发一条测试推送 |

响应固定 `Content-Type: application/json; charset=utf-8`、`Cache-Control: no-store`；
token 不对或端点没开一律 `403`（`{"error":"bad token"}` / `{"error":"endpoint disabled"}`），
`cmd` 不在白名单返回 `400`。

安全提示：uhttpd 开了 `rfc1918_filter`，端点只在局域网可达（别往公网映射）；
**必须设置 token**；子命令以只读为主，`check` 会触发一次真实查询，`wechat-enable`
会写推送配置 —— 两者都是有意开放给"持有 token 的机器人/局域网设备"的。

## 配置项

`config powerfee 'main'`：

| 选项 | 默认 | 说明 |
|---|---|---|
| `enabled` | 1 | 总开关，0 = 只保留命令行查询 |
| `room_num` / `campus` / `building` / `room` | 空 | 默认（单房间）房间，用 `powerfee set-room` 自动填；有 `config room` 段时只作兜底 |
| `interval` | 1800 | 查询间隔（秒），默认 30 分钟 |
| `retry_interval` | 300 | 查询失败后的重试间隔（秒） |
| `active_hours` | 空 | 查询时段（留空 = 全天）。如 `07:00-23:00`、`07:00-12:00 14:00-23:00`、跨天 `22:00-06:00` |
| `threshold` | 20 | 低于该值告警 |
| `warn_ratio` | 2 | 低于 `threshold × 该值` 进入预警区 |
| `cooldown` | 180 | 同一档位的重复提醒间隔（分钟） |
| `notify_warn` | 1 | 进入预警区是否也发邮件 |
| `notify_recovery` | 1 | 充值恢复后是否发一次邮件 |
| `notify_error` | 1 | 监控失效（长时间取不到数据）是否发邮件 |
| `stale_hours` | 6 | 超过这么多小时没取到数据算监控失效（窗口外的时长不计入） |
| `report_time` | 空 | 每天几点发「今日用量 + 曲线」报告（`HH:MM`，如 `21:00`；留空 = 不发）。到点给每个房间各发一份，与查询时段/告警互不干扰 |
| `report_days` | 7 | 报告与告警邮件里曲线的天数 |
| `log_file` / `log_max_kb` | /etc/powerfee/powerfee.log / 128 | 运行日志与轮转阈值 |
| `state_dir` | /etc/powerfee | 状态与历史目录（一般不用改） |

`config room 'room1'`（多房间，可选，见[多个房间](#多个房间不同收件人)）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `enabled` | 1 | 是否监控这个房间（0 = 保留配置与历史但不查） |
| `num` | 空 | **接口里的房间编号**（必填，`room-add` 会自动填） |
| `label` | 空 | 展示名；留空用「楼栋 房间」 |
| `campus` / `building` / `room` | 空 | 展示用，`room-add` 会从接口自动填 |
| `threshold` | 空 | 留空继承 `main.threshold` |
| `mail_to` | 空 | 留空继承 `mail.to`；多个逗号分隔 |
| `mail_account` | 空 | 用哪个命名发件账号（`config mail_account` 的段名）；留空继承 `mail` 段（1.2.0，见[多个发件邮箱账号](#多个发件邮箱账号)） |
| `notify_url` | 空 | 留空继承 `notify.url` |
| `notify_token` | 空 | 留空继承 `notify.token` |
| `notify_account` | 空 | 用哪个命名推送账号（`config push_account` 的段名）；留空继承 `notify` 段（1.2.0，见[多个推送账号](#多个推送账号)） |
| `notify_enabled` | 空 | 留空继承 `notify.enabled`；`1`/`0` 单独开关这个房间的推送 |

`config api 'api'`（**换学校只改这一段**）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `url` | 空 | 完整接口地址；留空则只记日志不查询 |
| `method` | POST | `POST` / `GET` |
| `content_type` | application/x-www-form-urlencoded | POST 的 Content-Type |
| `body` | 空 | POST 的请求体 |
| `timeout` | 40 | 单次请求超时（秒） |
| `dns_servers` | 223.5.5.5 119.29.29.29 | 本机 DNS 解不出接口域名时用的备用 DNS |
| `rooms_path` | @.obj[*] | 房间数组的 jsonfilter 表达式 |
| `ok_path` / `ok_value` | 空 / true | 成功标志；`ok_path` 留空=不检查 |
| `msg_path` | 空 | 错误信息字段 |
| `field_id` / `field_name` / `field_building` / `field_campus` / `field_balance` | roomNum / room / building / schoolArea / powerBalance（仅为默认值，按你的接口改） | 房间对象字段名 |
| `unit` | 度 | 余额单位（度 / kWh / 元 …）。接口返回里有单位字段时以接口为准 |
| `unit_field` | du | 余额单位的**字段名**：接口返回里读到就用它（拿不到才用 `unit`）；留空 = 只用 `unit` |

`config api 'api'` 里的**每日用电量接口**（可选，1.2.0，见[每日用电量与曲线](#每日用电量与曲线)）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `daily_url` | 空 | 逐日接口地址；**留空 = 不启用**（自动退回本地采样估算） |
| `daily_body` | `roomNum={room}&lastDate={month}&type=2&pageNum={page}&pageSize={page_size}` | 请求体模板，占位符 `{room}` `{month}` `{page}` `{page_size}` |
| `daily_method` / `daily_content_type` | POST / application/x-www-form-urlencoded | 请求方式与类型 |
| `daily_timeout` | 30 | 单次请求超时（秒；告警邮件里现画曲线时会压到 10 秒） |
| `daily_page_size` | 31 | `{page_size}` 的取值（一个月最多 31 天，一页够） |
| `daily_path` | @.obj.dailyDetailsInfos[*] | 逐日明细数组的 jsonfilter 表达式（每个元素一行） |
| `daily_ok_path` / `daily_ok_value` | @.ret / true | 成功标志；`daily_ok_path` 留空 = 不检查 |
| `daily_msg_path` | @.msg | 错误信息字段 |
| `daily_date_field` / `daily_used_field` / `daily_total_field` | dateTime / dailyUsed / totalUsed | 日期（`YYYY-MM-DD`）/ 当日用量 / 终身累计（跨月补齐要用） |
| `daily_unit_field` | dailyUsedUnit | 单位字段名（拿不到就用 `api.unit`） |

`config mail 'mail'`：`enabled`、`transport`（`auto`/`python3`/`msmtp`）、`host`、`port`、
`security`（`ssl`/`starttls`/`none`）、`user`、`password`、`from`、`from_name`、`to`、`tls_verify`。

`config notify 'notify'`（通知推送与查询端点，默认全关）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `enabled` | 0 | 推送总开关（与 `mail.enabled` 互相独立） |
| `url` | 空 | 要推送到的 HTTP 地址 |
| `method` | POST | `POST` / `GET`（GET 时渲染结果拼在 url 后面） |
| `content_type` | application/json | POST 的 Content-Type |
| `body` | `{"text":"{text}"}` | 请求体模板（占位符见[推到微信 / QQ](#推到微信--qq通知推送-webhook)；报告推送另有 `{chart}` 文本曲线） |
| `token` / `token_header` | 空 / Authorization | 可选鉴权：token 放进指定请求头 |
| `timeout` | 15 | 推送请求超时（秒） |
| `http_enabled` | 0 | 是否开启查询端点（CGI） |
| `http_token` | 空 | 查询端点要求的 token；留空 = 端点关闭 |

`config mail_account 'qq_main'`（命名发件账号，可选，见[多个发件邮箱账号](#多个发件邮箱账号)）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `enabled` | 1 | 是否启用这个账号（`0` = 停用） |
| `transport` | 空 | `auto` / `python3` / `msmtp`；留空继承 `mail.transport` |
| `host` / `port` / `security` | 空 | SMTP 服务器 / 端口 / 加密方式；留空继承 `mail` 段的同名项 |
| `user` / `password` | 空 | 登录账号与授权码；留空继承 `mail` 段的同名项 |
| `from` / `from_name` | 空 | 发件人与显示名；留空继承 `mail` 段的同名项 |
| `tls_verify` | 空 | 留空继承 `mail.tls_verify` |

`config push_account 'sc_room1'`（命名推送账号，可选，见[多个推送账号](#多个推送账号)）：

| 选项 | 默认 | 说明 |
|---|---|---|
| `enabled` | 1 | 是否启用这个账号（`0` = 停用） |
| `channel` | 空 | `serverchan` / `wecom` / `clawbot` / `custom`；留空继承 `notify` 段（按 URL 判断通道） |
| `url` | 空 | 推送地址；留空继承 `notify.url`；`channel=clawbot` 且留空时用本机 ClawBot API |
| `method` / `content_type` / `body` | 空 | 请求方式 / 类型 / 模板；留空继承 `notify` 段的同名项 |
| `token` / `token_header` | 空 | 鉴权令牌与请求头；留空继承 `notify` 段的同名项 |
| `timeout` | 空 | 推送超时（秒）；留空继承 `notify.timeout` |
| `bot_id` | 空 | `channel=clawbot` 时的 bot id |

改完 `uci commit powerfee` 即生效（守护进程每轮都重读配置，**不用重启服务**；间隔改动在下一轮生效）。

## 网页界面（LuCI）

装上 `luci-app-powerfee` 后，路由器后台的 **服务 → 宿舍电量哨兵** 下有五个页面：

| 页面 | 做什么 |
|---|---|
| **状态** | 余额（按档位着色）、档位、日均用量、预计可用天数、上次查询 / 上次提醒、累计查询次数、**查询时段与「现在是否在时段内」**；多房间时按房间列出余额（房间/编号/档位/阈值/上次查询/送达方式/**发件账号与推送账号/通道**）；**最近 7 天用电量小卡片**（合计 / 日均 / 缺几天，点它进「用电量」页；取不到数据时整块不显示）；邮件通道状态；服务开关（启动 / 停止 / 重启）；三个动作按钮：**立即查询**（查询时段外会提示已跳过）、**发送测试邮件**、**运行自检**；底部是最近日志 |
| **用电量** | **逐日用电量曲线**（后端渲染的 PNG，与报告邮件里的图同源；拿不到图时退回文本曲线）+ **逐日数据表**（日期 / 用量 / 备注，逐日标出「缺数据」「采样不完整」）+ 摘要（数据来源 / 时间范围 / 合计 / 日均 / 最高 / 最低 / 统计）；**数据来源**是两色徽标：**学校接口逐日数据**（绿）与**本地采样估算**（橙，注明「仅供参考」）。顶部**选房间**（多房间时）、**选范围**（最近 7 / 14 / 30 天，或某个自然月 `YYYY-MM`）与**刷新** —— 只在你点刷新 / 换房间 / 换范围时取一次，不轮询；底部**「立刻发一份报告」**（等同 `powerfee report`：邮件带图 + 推送单行摘要，走房间自己的收件人 / 发件账号 / 推送通道） |
| **宿舍管理** | 已配置房间的列表（余额、档位、阈值、送达方式含**发件 / 推送账号**、上次查询）与**增删改**：编辑弹窗里可以单独设置每个房间的**收件邮箱 / 发件账号 / 推送地址 / 推送令牌 / 推送账号 / 阈值 / 展示名**，还能单独停用某个房间；下面是按房间号搜索或按「校区 → 楼栋」逐级浏览，**勾选（可多选）后点「添加选中的 N 个」即可一次加入多个房间**（已经在监控里的会标出来并自动跳过；老的单房间配置可以一键「加入多房间管理」，状态与历史会一起继承） |
| **设置** | **监控参数**（页签「监控与提醒」（开关 / 间隔 / 失败重试 / **查询时段** / 阈值 / 预警倍数 / 冷却 / 三个提醒开关 / 监控失效判定）/「**定时报告**」（报告时间 `report_time`、曲线天数 `report_days`）/「日志」（`log_file`、`log_max_kb`））、**查询接口**（页签「接口地址」（换学校只改这一组：URL / 方法 / Content-Type / 请求体 / 超时 / 备用 DNS / 房间数组与字段名映射）/「**逐日用电量接口**」（`daily_url`、`daily_body`、`daily_method`、`daily_content_type`、`daily_timeout`、`daily_page_size`）/「高级映射」（`daily_path`、`daily_ok_path`、`daily_ok_value`、`daily_msg_path`、`daily_date_field`、`daily_used_field`、`daily_total_field`、`daily_unit_field`、`unit_field`，一般不用改））、邮件提醒（默认发件账号的 SMTP 参数）、通知推送（webhook / 查询端点）；页面底部是**「邮箱账号（额外）」**区：多套发件账号的增删改与发测试邮件（密码只显示「已设置」掩码） |
| **微信推送** | 顶部是**推送账号**区：命名推送账号（通道 / 掩码地址 / 状态）的增删改与发测试消息；下面是**推送通道选择器**：**Server酱**（填一个 SendKey）/ **企业微信群机器人**（粘 Webhook 地址）/ **ClawBot**（原来的五步向导：装服务 → 页面里直接显示登录二维码 → 在微信里发一条消息激活 → 一键启用推送）/ **自定义 webhook**（url + method + content_type + body）/ **关闭**。每个通道都是「启用并测试」一次做完，结果（含服务端原文）直接显示在页面上；状态卡显示当前通道、开关、掩码地址与最近一次推送 |

界面上的按钮走的是 `powerfee json ...` / `powerfee push ...` / `powerfee wechat ...`
这几条机器接口，与命令行完全同源；`/usr/share/rpcd/acl.d/luci-app-powerfee.json` 只授予必要权限
（读写 `powerfee` 配置、执行 `/usr/bin/powerfee`、`/etc/init.d/powerfee`
以及微信服务的 `/etc/init.d/weclawbot-api`）。

## 命令

```
powerfee check              查一次余额，必要时发邮件（服务默认每 30 分钟自动执行）
powerfee check --force      无视冷却、档位与查询时段，强制发一封当前状态邮件（验证邮件通道用）
powerfee status             显示余额、状态、日均用量、上次提醒与查询时段（多房间时逐个房间列出）
powerfee rooms [关键词]      列出接口返回的房间（可按房间号/楼栋/校区过滤）
powerfee find <关键词>       同上（别名）
powerfee set-room <房间号>   设置要监控的房间（单房间模式）
powerfee room-list          列出已配置的房间（余额 / 收件人 / 推送通道）
powerfee room-add <房间号>   添加一个房间（--id/--num/--label/--mail-to/--notify-url/--notify-token/--threshold）
powerfee room-add <编号1> <编号2> …   批量添加多个房间（只请求接口一次；已在监控的按编号跳过，逐房间报告）
powerfee room-remove <段名>  删除一个房间（连同状态与历史）
powerfee room-set <段名> <键> <值>   改房间字段（num/label/threshold/mail_to/mail_account/notify_url/notify_token/notify_account/notify_enabled/enabled）
powerfee mail-account <子命令>  命名发件账号管理：list / add <名> / set <名> <键> <值> / remove <名> / test <名>
powerfee push-account <子命令>  命名推送账号管理：list / add <名> / set <名> <键> <值> / remove <名> / test <名>
powerfee test-mail [房间]    发送一封测试邮件
powerfee notify-test [房间]  发送一条测试通知（webhook 推送通道）
powerfee history [房间] [条数]  查看历史采样与日均估算
powerfee usage [房间] [选项]  每日用电量曲线与摘要（--days N / --month YYYY-MM / --date YYYY-MM-DD / --png 路径）
powerfee report [房间] [--days N]  发一份「今日用量 + 最近 N 天曲线」的报告（邮件 + 推送；不带房间 = 每个房间各发一份）
powerfee log [行数]          查看运行日志
powerfee json <子命令>       机器可读输出（LuCI 界面 / 查询端点用的就是它）
powerfee push <子命令>       推送通道管理：Server酱 / 企业微信 / ClawBot / 自定义 / 关闭
powerfee wechat <子命令>     微信（ClawBot）推送向导：status / qr / enable / test
powerfee selftest           运行判定逻辑自检（122 项）
```

`powerfee json` 子命令：`status` / `brief` / `groups` / `rooms [关键词] [条数]` /
`room-list` / `room-add <房间号> [--id 段名] …`（也可以一次给多个编号 = 批量添加，
加 `--batch` 时单个目标也走批量：返回里多出 `results[]` 与 `added/skipped/failed/total` 计数） /
`room-remove <段名|编号>` /
`room-set <段名|编号> <键> <值>` / `room-save <段名|编号> <键=值> …` /
`mail-accounts` / `push-accounts`（1.2.0，命名账号清单，密钥只回掩码） /
`history [条数]` / `usage [房间] [--days N|--month M|--date D] [--png]`（1.2.0，每日用电量） /
`log [行数]` / `check` / `test-mail [房间]` / `notify-test [房间]` /
`set-room <房间号>` / `selftest`。

`json usage` 返回（给机器人用；`png_base64` 只在加 `--png` 时才给）：

```json
{"ok":true,"source":"api","unit":"度","empty":false,"from":"2026-10-01","to":"2026-10-07",
 "days":[{"date":"2026-10-01","used":11.0,"incomplete":false}, …],
 "summary":{"total":98.0,"avg":14.0,"max":17.0,"max_date":"2026-10-07","min":11.0,"min_date":"2026-10-01","count":7,"missing":0,"incomplete":0},
 "text":"每日用电量（度）\n17 ┤      ██\n…","summary_line":"合计 98.00 度 · 日均 14.00 度","source_text":"学校接口逐日数据"}
```

`json status` 在旧字段之外新增了 `active_hours` / `in_active_window` / `active_hours_valid` /
`room_count` / `rooms`（每个房间的余额、档位、收件人、掩码后的推送地址）；
1.2.0 起每个房间对象里还带**发件账号 / 收件人 / 推送账号 / 通道**字段（未指定账号时为空，
表示用默认 `mail` / `notify` 段）。`json check`
在窗口外跳过时会返回 `skipped=true` 与 `in_active_window=false`（推送地址与令牌只回掩码/是否已设置）。

`powerfee push` 子命令（LuCI「微信推送」页顶部的通道选择器用的就是它，需要 python3）：

```sh
powerfee push status                     # JSON：当前通道/开关/掩码地址/最近一次推送（不回密钥）
powerfee push set-serverchan <SendKey>   # Server酱：写配置 + commit + 立刻发测试
powerfee push set-wecom <url>            # 企业微信群机器人：同上
powerfee push set-webhook <url> [method] [content_type] [body]
powerfee push set-clawbot [--bot <id>]   # 切回 ClawBot（读 auth.json 写 url/token）
powerfee push off                        # 关掉推送（enabled=0，通道配置保留）
powerfee push test                       # 只发一条测试消息，不改配置
```

`powerfee wechat` 子命令（LuCI「微信推送」页 ClawBot 通道用的就是它，需要 python3）：

```sh
powerfee wechat status                   # JSON：服务形态/运行/绑定/激活/推送配置/最近一次推送
powerfee wechat qr --json                # 二维码（base64 PNG；默认输出裸 PNG 到 stdout）
powerfee wechat qr --out /tmp/qr.png     # 存成文件
powerfee wechat enable [--bot <id>]      # 一键：写 notify 配置 + commit + 发测试消息
powerfee wechat enable --dry-run         # 只检查（不写配置、不发消息）
powerfee wechat test                     # 只发一条测试消息
```

服务管理：`/etc/init.d/powerfee start|stop|restart|enable|disable`（或 `service powerfee ...`）。

## 判定与告警规则

| 档位 | 条件 | 邮件头颜色 |
|---|---|---|
| LOW | 余额 < `threshold` | 红 |
| WARN | 余额 < `threshold × warn_ratio` | 橙 |
| OK | 其余 | 绿 |
| UNKNOWN | 查询失败 | 灰 |

提醒时机：

1. **档位变差**立即提醒（`notify_warn=0` 时预警档静音）
2. **持续低电量**每 `cooldown` 分钟重复提醒一次
3. **充值恢复**（从 LOW 回到 WARN/OK）提醒一次
4. 监控失效（超过 `stale_hours` 没取到数据）提醒一次，恢复后再补一条"已恢复"

日均用量用最近 72 小时采样估算（采样不足 2 个点、时间跨度不足 3 小时、或余额没减少时不给结论）。

## 邮件长什么样

主题：

```
⚠️ 宿舍电费不足：1号楼 A101 仅剩 8.22 度
🔔 宿舍电量接近阈值：1号楼 A101 剩余 35.40 度
✅ 宿舍电量已恢复：1号楼 A101 剩余 55.30 度
❗ 宿舍电量监控异常：已 8 小时未取到数据
📊 每日用电报告：1号楼 A101 今日 12.4 度
```

正文是 `multipart/alternative`（纯文本 + HTML 两版），内容示例：

```
宿舍电量提醒
============

房间：1号楼 A101（东区）
状态：⚠️ 电费不足
当前余额：8.22 度
告警阈值：20 度
日均用量：约 3.20 度/天（近 72 小时估算）
预计可用：约 2.6 天

请尽快充值，避免余额耗尽停电。

查询时间：2026-10-07 15:23:11 CST
监控设备：OpenWrt（宿舍电量哨兵 OpenWrt 版 v1.2.4）

本邮件由路由器自动发送，请勿回复。
```

1.2.0 起，**余额告警邮件与用电报告**的 HTML 版还会带上用量曲线：结构变成
`multipart/related`（内层还是上面的 `multipart/alternative`），曲线 PNG 用
`Content-ID` 内联（`<img src="cid:...">`，客户端里直接显示、**不是附件**，
约 3 KB）；生成不了 PNG 时退回纯 HTML 表格图。测试邮件与监控失效邮件不带图。

## 实现要点（踩过的坑）

**解析 JSON**：用 OpenWrt 自带的 `jsonfilter`。字段提取用 awk 按 `"key": "value"` 匹配，
对字段顺序不敏感；房间数组逐个元素处理，所以**字段顺序、多余字段都不影响**。

**DNS 回退**：有些 OpenWrt 的 dnsmasq 会把某个域整域指到不通的校内 DNS
（例如 `server=/xxx.edu.cn/10.x.x.x`），那个 DNS 一不通，域名就解析不了，
`curl` 直接退出码 6。所以脚本在解析失败时会自动用 `api.dns_servers` 里的公共 DNS
解析出 IP，再用 `curl --resolve` 直连 —— 监控不受路由器 DNS 配置影响，日志里会留一行记录。

**发邮件**：curl 的 OpenWrt 构建**不带 SMTP 协议**（`Protocols: file ftp ftps http https mqtt mqtts`），
所以邮件走两条通道，自动探测：

1. `python3`（`smtplib`）—— 装了 `python3-email` / `python3-openssl` 就能用；
2. `msmtp` —— OpenWrt 常见的小型 SMTP 客户端，`apk add msmtp` 即可（适合没装 python3 的路由器）。

邮件正文在 shell 里拼成标准 RFC822：中文主题用 RFC2047 `=?UTF-8?B?...?=`，
正文用 base64，行尾严格 CRLF（busybox 没有 `base64`/`fold`，用 `openssl base64` + `awk` 转换）。

**状态持久化**：`/etc/powerfee/state`（权限 600）保存上次余额、档位、上次提醒时间等，
重启不丢，所以重启路由器不会重复发提醒。历史采样在 `/etc/powerfee/history.csv`
（保留最近 7 天、最多 600 点）。

**ash 没有函数作用域**：脚本里所有内部变量都用 `local` 声明。这不是洁癖 ——
开发时 `mail_alert` 里的 `_reason="$1"` 覆盖了调用方的 `_reason`，导致"监控已恢复"邮件
重复发了两封，是实测抓出来的。

**LuCI 的 `ui.createHandlerFn` 参数顺序新旧相反**（1.2.3 修）：新版（26.x）把**事件放在最后**
（`(…附加参数, 事件)`，源码里就是 `arguments[args.length].currentTarget`），
老版（23.05 及更早）放在**最前**（`(事件, …附加参数)`）。按老顺序写 `function(ev, room, fields)`
在 26.x 上会变成 `room = 事件`、`fields = 那个房间`，于是 `fields.enabled` 是 undefined →
`TypeError: Cannot read properties of undefined (reading 'checked')`，**异常在发出请求之前就抛**，
表现是「点保存没反应」。视图里所有带附加参数的处理器统一走 `handlerArgs(arguments)`
（按 `currentTarget` 认出事件并丢掉，只留附加参数），两代 LuCI 都认。

**`$(...)` 在路由器上很贵**（1.2.4 的性能优化）：一次 `$(命令)` 就是一次 fork，本机实测约
1.5 ms。原来的 `json status` 每次要拼几百个 JSON 字段，字段值全走 `$(json_str ...)` → 一次调用
**三百多次 fork**，实测 2.0 s；`json room-list` 1.5 s。改成「先算进全局变量、再 printf」
（`jstr` / `jnum` / `jbool`，纯 shell 零 fork）后：`json status` 2.0 s → **1.1 s**、
`json room-list` 1.5 s → **0.7 s**，**输出逐字节不变**（12 个命令的输出两两对拍过）。
结论：脚本热路径里别用 `$(...)` 包函数取「拼 JSON 用的小值」，用全局变量传值。

**LuCI 的静态资源带 `?v=` 版本戳**：改完界面文件要 `rm -f /tmp/luci-indexcache*.json`
（并重启服务让守护进程用上新脚本），否则页面可能还在用缓存的旧文件。

## 常见问题

**收不到邮件怎么排查？**

```sh
powerfee test-mail          # 先发一封测试邮件
powerfee log 30             # 看最近日志（发送失败的原因会写在这里，不会静默）
logread -e powerfee         # 同样的日志也在 syslog 里
```
常见原因：授权码填成了登录密码（QQ/163 必须用授权码）；`mail.enabled` 还是 0；
收件人没填；路由器出不了外网。

**`powerfee rooms` 说"尚未配置学校接口"？**
`api.url` 还是空的。见[适配你的学校](#适配你的学校)；注意 `uci set` 建 section 要先
`uci set powerfee.api=api`，URL 里的 `&` 要加引号。

**`powerfee rooms` 报错或者列表是空的？**
说明 `rooms_path` 或 `field_*` 没填对。先看接口原始返回（浏览器 F12 或 `curl`），
再对照[第二步](#第二步看返回结构填映射)检查 `rooms_path` 指向的那一层是不是房间数组。

**`powerfee status` 显示"服务状态：inactive"？**
服务没启动：`/etc/init.d/powerfee start`。要开机自启：`/etc/init.d/powerfee enable`。

**余额一直没更新 / 上次查询是几小时前？**
先手动跑一次 `powerfee check` 看报错。若是 DNS 问题脚本会自动回退，日志里会写；
若是接口本身变了（学校改版），需要更新 `api.url` 或字段映射。
如果设过 `active_hours`（查询时段），窗口外的「上次查询」本来就不会更新 ——
`powerfee status` 会显示「现在不在时段内，暂停查询」，这是正常现象。

**日志里一直「本机 DNS 解析失败，改用 … 直连」，然后超时（`curl 退出码 28`）？**
这是**校内接口域名解析到内网 IP** 时最容易踩的坑：学校的 DNS 把接口域名解析成
`10.x` / `172.16~31.x` / `192.168.x` 这类内网地址，而 OpenWrt 的 dnsmasq 默认开着
**DNS-rebind 保护**，会把「上游返回内网地址」当成 rebind 攻击**直接拒答**。表现就是：

- `nslookup 你的接口域名 127.0.0.1` → 没有答案
- `nslookup 你的接口域名 <学校DNS>` → 正常（因为绕过了 dnsmasq）
- `logread -e dnsmasq` → `possible DNS-rebind attack detected: 你的接口域名`

两条修法（建议都做）：

```sh
# 1) 把这个域名从 rebind 保护里放行（根因修复，整台路由器受益）
uci add_list dhcp.@dnsmasq[0].rebind_domain='你的接口域名'
uci commit dhcp && /etc/init.d/dnsmasq restart

# 2) 让 powerfee 的 DNS 回退也用能解析该域名的 DNS（校内 DNS，通常由校园网 DHCP 下发）
uci set powerfee.api.dns_servers='学校DNS 223.5.5.5'
uci commit powerfee
```

为什么第 2 条也必要：脚本在解析失败时会用 `api.dns_servers` 兜底（默认是公共 DNS），
而**公共 DNS 给的是该域名的公网地址**——从校园网里往往反而连不通，于是你看到的是
「解析失败 → 换公网 IP 直连 → 超时」的连锁症状。把学校 DNS 排在前面，兜底才会给出
正确的内网地址。

**怎么换房间？**
单房间模式：`powerfee set-room <房间号>`（会清空上一个房间的运行状态与历史曲线，
避免把两个房间的余额连成一条没有意义的曲线）。
多房间模式（有 `config room` 段）：用 `powerfee room-set <段名> num <编号>` 改编号，
或 `powerfee room-remove <段名>` 删掉再 `powerfee room-add`。

**路由器上没有 python3，装 msmtp 又报 `wget: exited with error 1`？**
某些 OpenWrt 固件里 `/usr/bin/wget` 是 **wget-nossl**（编译时没带 TLS），而 apk 下载索引时
是调用外部 wget 的 —— 于是**所有 https 源都必然失败**（表现为 `unexpected end of file`，
容易被误判成"网络不稳"）。两条出路：把源换成同镜像的 http 地址（apk 仍会校验索引签名，
安全性不降级），或者装 `wget-ssl` 替换掉 wget-nossl。

**占多少资源？**
按接口大小而定（有的学校一次返回全校房间、响应近 1 MB，30 分钟一次约 45 MB/月）；
CPU 峰值只在一瞬间，内存占用可忽略；状态文件几 KB，日志默认超过 128 KB 自动轮转成 `.old`。

**固件升级后配置还在吗？**
`/etc/config/powerfee` 本来就会被保留；`/etc/powerfee`（状态与历史）由安装脚本/包加进了
`/etc/sysupgrade.conf`，也会保留。

**会不会误报 / 刷屏？**
同一档位有冷却时间（默认 180 分钟），档位没变化不会重复发；查询失败也不会乱报，
只有连续 `stale_hours` 小时取不到数据才发一次"监控异常"，恢复后补一条"已恢复"。

## 已验证范围

在 OpenWrt 25.12.2 (x86_64) 上真机实测通过：

- **功能测试 23 项**：判定逻辑自检（122 个用例）、真实接口查询、DNS 回退、房间搜索与设置、
  状态/历史输出、邮件 MIME 结构可被标准库解析、冷却期不重复发、恢复只发一封、状态文件权限
- **服务测试 10 项**：procd 常驻与自动拉起、实际轮询节奏、改配置不重启即生效
  （实测间隔从 62 秒变 31 秒）、监控失效告警与恢复邮件、开机自启、重启后状态保留
- **邮件通道 8 项**：`python3`（smtplib）与 `msmtp`（1.8.32，GnuTLS）两条通道各自验证了
  `ssl` / `starttls` / 无认证三种连接方式、认证失败不静默、`tls_verify=1` 时确实拒绝自签证书
- **通知推送与查询端点 36 项**（`tools/test_notify.py`，假 webhook + 真实接口）：
  8 个 reason（low / repeat-low / warn / recovered / stale / stale-ok / force / test）全部推到，
  `mail.enabled=0` 时照推；11 个占位符渲染与 JSON 转义、中文不乱码、token 请求头、GET 百分号编码；
  失败写日志不静默且不影响邮件；查询端点的 token/开关/白名单/路径穿越、各子命令、POST 表单、中文关键词
- **查询时段与多房间 24 项断言**（`tools/test_json.py` 的隔离用例：脚本与配置全部放在 `/tmp/pf_wip`，
  用假学校接口 + 假 webhook + 假 SMTP 跑，生产配置/服务全程不动）：
  时段解析的边界/跨天/多段/非法输入（22 个 selftest 用例）；窗口外 `check` 与守护进程
  **零请求**、窗口内按间隔恢复查询；`check --force` 无视时段；窗口外时长不计入失效判定
  （对照组：没有时段时照旧发 stale 告警）；三个房间一次请求分发、各自档位/冷却/收件人/通道独立、
  三个房间各发一封到自己的收件人；`room-add` / `room-set` / `room-remove` / `json room-list`；
  老配置（无 `config room`）回归：状态文件仍是 `state`/`history.csv`、行为与旧版一致；
  迁移：老 `state`/`history.csv` 继承给第一个房间且不重复发提醒

- **批量添加房间 17 项断言**（2026-10-08 新增，`tools/test_json.py` 的隔离用例，同样跑在
  `/tmp/pf_wip`；假接口在 127.0.0.1:8899，用它的命中日志数请求次数）：
  一次 `room-add 1001 2002 3003` **只发生 1 次接口请求**、三个段都建对（num/campus/building/room
  都来自接口）、选项对整批生效；已经在监控的编号再传一次 → 报「已在监控」跳过且**不新建段**
  （三个一起传也一样）；同一批里重复给同一个编号只建一个段；**部分失败不整体失败**：混入一个
  不存在的编号 → 成功的仍加上、失败的单独报出、汇总行给出计数、**退出码非 0**；`--id 前缀`递增
  （dorm / dorm2）且指定段名被占用时失败、已有段**原样不动**；`json room-add --batch` 返回
  `results[]`（逐房间 target/num/ok/skipped/id/message）与 `added/skipped/failed/total` 计数；
  **单房间老行为逐字符对照**：`connect()` 之后、覆盖 `/usr/bin/powerfee` **之前**先把路由器上
  那份只读复制到 `/tmp/pf_wip_old`，在同样的隔离配置上跑 4 组单房间命令（含 `--id` / `--num` /
  `json` 形式），输出 / 退出码 / 写入的段与新脚本**逐字符一致**（路由器上装的若已是带批量实现
  的新版，对照组自动**跳过并打印说明** —— 不拿新版跟新版比、也不把自比自说成「一致」）；
  生产配置指纹跑前跑后**逐字符一致**

- **每日用电量与曲线 33 项断言**（2026-10-08 新增，`tools/test_json.py` 的隔离用例，同样跑在
  `/tmp/pf_wip`；假逐日接口在 127.0.0.1:8898，**复刻了「历史月最后一天不返回」这个坑**，
  且逐日用量与终身累计自洽、测试侧独立算期望值）：
  - **当月**（接口返回到昨天）与**历史月**（整月天数齐全）两条路径；**跨月补齐**：用下个月首日的
    数据把历史月最后一天恢复出来，与独立算的公式值一致（`used = total(下月1日) − total(上月最后返回日) − used(下月1日)`）
  - **缓存**：历史月落 `<state_dir>/usage.<房间段名>.<YYYY-MM>.json`，第二次跑**零新请求**（逐日接口请求数不变）
  - **兜底**：`daily_url` 留空、或这个房间在逐日接口里没有数据 → 自动退回本地采样估算，图照画
  - **充值跳变**：采样里塞一次 +100 充值，那天**没有负用量**、也没把 100 度算成用量；完整一天 = 12.00 度
  - `--date` 指定某天打印那天的用量；`--png` 写出的文件是合法 PNG（魔数校验）
  - `json usage` 是合法 JSON（`text` 文本曲线 + `days` 逐日数组 + `summary`；不加 `--png` 时**不含** `png_base64`）
  - **定时报告**：把 `report_time` 设成「现在」、`active_hours` 设成「之后」——报告在**查询时段之外**
    照样发出来（窗口外零请求，日志写明「定时报告时间到」）；邮件是 `multipart/related` +
    `Content-ID` 内联 PNG（HTML 里 `<img src="cid:…">`、PNG 段解码魔数正确、`Content-Disposition: inline`），
    推送里 `{chart}` 是多行文本曲线；余额告警邮件同样带内联曲线
  - 生产配置指纹（房间/账号段数、两个总开关、`/usr/bin/powerfee` 的 md5、`/usr/lib/powerfee/` 文件清单、
    `daily_` 选项条数）跑前跑后**逐字符一致**
- **真实逐日接口核对**（2026-10-08，真实学校接口，一次性）：上月返回 29 天（**最后一天确实不返回**）、
  补齐值 29.83 度与「独立再打接口 + 手算公式」的结果一致；抽 2 天（月初 21.83 / 月末 29.46）与原始
  响应逐位一致；当月 `--days 7`（7 点、非负）与 `--date` 正常；历史月缓存落盘
- **微信桥接（powerfee-chat）**：`--selftest` **49/49**（新增 8 项曲线用例：关键词优先于「电量」、
  调 `json usage --days 7`、多行文本曲线 ≤12 行、纯「电量」仍是单行摘要的回归）；另做了一次端到端注入：
  一条「曲线」消息 → 桥接调真 `powerfee json usage --days 7` → 假 ClawBot API 收到多行文本曲线
  （路径 `/bots/<id>/messages`、`Authorization: Bearer …` 正确）
- **LuCI 界面**：**五个页面**在真机上 HTTP 200、菜单与索引缓存里可见；用真实会话走过 rpcd 的
  `file exec` / `uci` 读写 / `service list`，并用一条越权调用（`file exec /bin/ls`）做反面对照
  确认 ACL 真的在拦；浏览器里真点过「运行自检」按钮（当时返回的是那一版的项数）
- **LuCI 页面行为（1.2.3，无头 Edge + CDP 驱动真实页面，不是桩件）**：编辑房间 → 保存 →
  通知「已保存」、**0 条 JS 异常**（修 `createHandlerFn` 参数顺序之前，这里抛的是
  `TypeError: Cannot read properties of undefined (reading 'checked')`，请求根本没发出去）；
  停用 → 通知「已停用」且列表**即时刷新**（按钮变「启用」）→ 再启用 → 「已启用」；
  五个页面逐一打开均 **0 异常**；「用电量」页在真实浏览器里画出 PNG 曲线（数据来源＝学校接口
  逐日数据）+ 7 天逐日表，「刷新」按钮可重新取数
- **微信推送页**：在装了 Docker 形态 `weclawbot-api` 的真机上验证过 `powerfee wechat status`
  （服务在跑 / 已绑定 / context_token 就绪 / 推送已配），`wechat qr` 能把日志里的字符画二维码
  还原成 360×360 的 PNG 并被解码器还原出 `https://liteapp.weixin.qq.com/q/...`（同一张图在
  Docker 与原生（syslog 前缀）两种日志形态下都验过）；`wechat enable` 从"未配置"状态一键写全
  notify 段并发出一条 HTTP 200 的测试消息；CGI 侧 `wechat-status/-qr/-enable/-test` 四个子命令
  与 400/403/404 分支也都跑过。原生包（`/etc/init.d/weclawbot-api` + `/etc/weclawbot/config/`）
  的真机形态尚未落地，用同一套日志/配置在本地做了等价模拟（见 docs/luci-wechat-page.md）
- **安装包**：`.apk` 在 25.12 真机上装/卸过；`.ipk` 的结构与官方包（18.06.9 ~ 24.10.2）逐项
  对照过，并用**真 opkg**（24.10.2 的 opkg 0.4.0 + 24.10 的 functions.sh/rc.common）在 chroot 里
  实装 / 升级 / 卸载验证过：`opkg files`、conffiles 的 sha256 记录、装完 `/etc/rc.d` 无 powerfee
  （= postinst 的 disable 生效）、卸载保留 `/etc/powerfee`、改过的 conffile 由 opkg 保留
  （`Not deleting modified conffile`），装出来的环境里 `powerfee selftest` 全过。
  详见 `openwrt/README.md` 第 5 节

- **性能（1.2.4，真机 x86_64 路由器）**：先量基线再动手 —— `json status` 2.0 s、
  `json room-list` 1.5 s、`json brief` 0.3 s、`json usage --days 7` 1.0 s；用 PATH 包装器
  数出「一次 status 要起 644 个外部进程」，再用 `sh -x` 追出真正的大头是几百次 `$(...)` 的
  fork（每个约 1.5 ms）。改成不 fork 的转义（`jstr`/`jnum`/`jbool`）后：`json status` **1.1 s**、
  `json room-list` **0.7 s**（−44% / −56%），并用逐字节对拍确认 12 个命令的输出与改前完全一致

未验证：非 x86 平台（mips/arm 路由器）、其它 OpenWrt 版本、真实邮箱投递
（需要你自己的授权码，`powerfee test-mail` 一跑就知道）、**真正的 opkg 设备**
（本机的 `.ipk` 是在 25.12 上搭 chroot 用真 opkg 验的，不是装在一台 opkg 路由器上）。

测试脚本在 `tools/`，是在真机路由器上跑的（假 SMTP 服务器 + 断言），用法见 `tools/README.md`。

## 文件清单

```
files/usr/bin/powerfee              主程序（ash，CLI + 核心逻辑 + 邮件拼装 + 通知推送 + wechat 子命令）
files/usr/lib/powerfee/mail.py      python3 投递助手（只依赖标准库）
files/usr/lib/powerfee/wechat.py    python3 微信助手（二维码解析 / 状态 / 一键配置，只依赖标准库）
files/usr/lib/powerfee/chart.py     python3 绘图模块（文本 / PNG / HTML 三种曲线，只依赖标准库）
files/usr/lib/powerfee/usage.py     python3 逐日用量助手（解析 / 跨月补齐 / 本地采样估算 / 渲染）
files/usr/lib/powerfee/push.py      python3 推送通道助手（Server酱 / 企业微信 / ClawBot / 自定义）
files/www/cgi-bin/powerfee          查询端点（uhttpd CGI，装到 /www/cgi-bin/powerfee）
files/etc/init.d/powerfee           procd 服务脚本
files/etc/config/powerfee           UCI 默认配置
install.sh                          路由器上的安装脚本（不装包时用）
openwrt/                            OpenWrt 包定义与构建脚本（build-apk.sh 出 .apk，build-ipk.sh 出 .ipk，
                                    build-sign.sh 自签名并生成自建仓库索引；
                                    powerfee-chat 桥接包的包定义在 openwrt/powerfee-chat/）
luci/                               LuCI 网页界面源文件（状态 / 用电量 / 宿舍管理 / 设置 / 微信推送）
apk/                                构建好的 .apk 安装包（未签名；OpenWrt 25.x 及以上）
ipk/                                构建好的 .ipk 安装包（OpenWrt 24.10 及以下，opkg 系）
signing/                            自签名材料：公钥 + repo/（已签名 .apk 与 packages.adb，见 docs/signing.md）
tools/                              真机测试脚本（见 tools/README.md）
docs/                               微信 / QQ 通知与查询集成配方、微信推送页说明、包签名说明（signing.md）
integrations/astrbot-plugin-powerfee/  AstrBot 插件：聊天里用 /电费 查询余额（见其 README.md）
integrations/wechat-clawbot-bridge/  微信 ClawBot 电量查询桥接（python3；已打成 powerfee-chat 包，
                                    见其 README.md）
```

## 实现时踩到的两个"看起来能用其实不生效"的坑

1. **msmtp 的证书校验关不掉**：msmtp 默认 `tls_trust_file system` + `tls_certcheck on`，
   所以「不写 trust file」**不等于**不校验 —— `tls_verify=0` 会静默失效。必须显式写
   `tls_certcheck off`。这个 bug 是拿真 msmtp 跑自签证书测试才抓出来的，桩程序发现不了。
2. **ash 函数没有作用域**：`mail_alert` 里的 `_reason="$1"` 覆盖了调用方的 `_reason`，
   导致"监控已恢复"邮件重复发两封。脚本里所有内部变量都必须 `local` 声明。

## 许可

- 本目录（路由器版：`files/`、`luci/`、`openwrt/`、`tools/`、`install.sh`）的代码是**新写的**，
  构建出的 .apk 包元数据里标的是 **MIT**。
- 仓库里 fork 自上游（Windows 托盘版）的部分**版权归原作者**，本项目不代为重新授权。
- 如果你要把这套东西用于分发，建议先明确一下许可（加一个 `LICENSE` 文件，或改成 GPL-2.0 等），
  并注意别把上游代码一起重新授权。

## 免责

个人自用的小工具，与任何学校官方无关。**只读查询**，不做任何充值或写操作。
