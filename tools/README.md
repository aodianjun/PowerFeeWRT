# tools/ —— 测试与构建脚本

这些脚本是在**真机路由器**上跑的测试套件（假 SMTP 服务器 + 断言），不是打包产物的一部分。
它们需要能 SSH 到一台已经装好 `powerfee` 的 OpenWrt 路由器。

## 先设置连接信息（必填）

为避免把路由器地址/密码写进仓库，脚本从**环境变量**读取连接信息：

| 变量 | 默认 | 说明 |
|---|---|---|
| `POWERFEE_HOST` | 192.168.1.1 | 路由器地址（IP 或域名） |
| `POWERFEE_USER` | root | SSH 用户 |
| `POWERFEE_PASS` | （空） | SSH 密码；留空则只尝试密钥/agent |

Linux / macOS：

```sh
export POWERFEE_HOST=192.168.1.1
export POWERFEE_PASS='你的路由器密码'
python3 tools/deploy_test.py
```

Windows（cmd，注意 `set "VAR=值"` 的引号写法，否则尾随空格会算进值里）：

```bat
set "POWERFEE_HOST=192.168.1.1"
set "POWERFEE_PASS=你的路由器密码"
python tools\deploy_test.py
```

## 脚本一览

| 脚本 | 作用 | 需要 |
|---|---|---|
| `fake_smtp.py` | 假 SMTP 服务器，支持 plain / starttls / ssl + AUTH，把收到的邮件落盘成 `.eml` | 在路由器上跑（python3） |
| `deploy_test.py` | 功能测试 23 项：上传文件、自检、真实接口查询、DNS 回退、房间搜索、状态/历史、邮件三种连接方式、冷却与恢复、MIME 结构 | 路由器 + 假 SMTP |
| `test_service.py` | 服务级测试 10 项：procd 常驻、轮询节奏、改配置即时生效、监控失效与恢复邮件、开机自启、重启后状态保留 | 路由器 + 假 SMTP |
| `test_msmtp_real.py` | 真 msmtp 分支测试 8 项（含证书校验必须真的生效） | 路由器装了 msmtp |
| `test_msmtp.py` | 桩 msmtp 分支测试（没装真 msmtp 时用） | `stub_msmtp.py` |
| `stub_msmtp.py` | 桩 msmtp：校验 rc 文件权限/指令，再转发给假 SMTP | — |
| `test_notify.py` | 通知推送与查询端点测试 36 项：假 webhook 收推送（模板/token/GET/失败路径/全部 reason 同源）+ `/cgi-bin/powerfee` 的 token 校验、白名单、各子命令；收尾会恢复配置与状态 | 路由器 + 真实接口 |
| `test_json.py` | 校验 `powerfee json` 各子命令输出是合法 JSON | 路由器 |
| `check_mail_html.py` | 把已发邮件取回本机，检查 HTML 标签配平、有无 shell 残留 | 路由器 + 假 SMTP |
| `finalize.py` | 端到端安装（跑 install.sh）+ 清理测试残留 + 恢复干净配置 | 路由器 |
| `parse_eml.py` | 在路由器上解析 `.eml`，打印主题与 MIME 结构（由 `deploy_test.py` 自动生成上传） | 路由器 |

## 典型流程

```sh
# 1) 先跑一遍功能测试（会自动上传最新代码、拉起假 SMTP、逐项断言）
python tools/deploy_test.py

# 2) 通知推送（webhook）与 HTTP 查询端点（假 webhook + 真实接口触发全部 reason）
python tools/test_notify.py

# 3) 服务级行为
python tools/test_service.py

# 4) 邮件通道（装了真 msmtp 时）
python tools/test_msmtp_real.py

# 5) 收尾：清理残留、恢复干净配置
python tools/finalize.py
```

测试会真的改路由器上的 `/etc/config/powerfee` 与 `/etc/powerfee/`，
`finalize.py` 负责把它们恢复成「未配置」状态；跑之前请确认这台路由器是可以随便折腾的。

`test_notify.py` 自带备份/恢复（测试前把配置备份到 `/root/powerfee.conf.mine`、
状态目录备份到 `/root/pf_state.bak`，无论成功失败都会在收尾恢复并重启服务），
但它是**独占测试**：跑的时候会临时停掉 powerfee 服务，别和别的测试同时跑。
