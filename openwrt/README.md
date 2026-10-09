# OpenWrt 打包说明（powerfee / luci-app-powerfee / powerfee-chat）

这个目录里是 OpenWrt 包定义与两个构建脚本：

| 脚本 | 产出 | 适用 | 需要 |
|---|---|---|---|
| `build-apk.sh` | `apk/*.apk` | OpenWrt **25.x 及以上**（apk-tools 3 的 ADB 容器） | OpenWrt SDK（glibc x86_64 Linux） |
| `build-ipk.sh` | `ipk/*.ipk` | OpenWrt **24.10 及以下**（opkg 系） | 只要 `sh` + `python3`（不需要 SDK） |

三个包**内容都是纯脚本**（ash 主程序、python3 投递助手/桥接、CGI、LuCI 的 JS/JSON，
没有任何架构相关二进制），只是**包格式**不同；一个 `all` 包在 armv7a / aarch64 / x86 /
x86_64 上都通用，不需要按架构分别打包（理由见 `build-ipk.sh` 头部注释与仓库 README
的「安装包与架构支持」）。

产物（构建后出现在仓库的 `apk/`、`ipk/` 目录）：

| 文件 | 内容 | 大小量级 |
|---|---|---|
| `apk/powerfee_1.2.4_all.apk` | 主程序：`/usr/bin/powerfee`、`/usr/lib/powerfee/{mail.py,pfcommon.py,push.py,wechat.py}`（投递助手 + 推送通道管理器 + 微信助手）、`/etc/init.d/powerfee`、`/etc/config/powerfee`、`/www/cgi-bin/powerfee`（查询端点） | ~30 KB |
| `apk/luci-app-powerfee_1.2.4_all.apk` | LuCI 界面（依赖 `powerfee`）：JS 视图（状态 / 选择宿舍 / 设置 / 微信推送：推送通道选择器 + ClawBot 五步向导）+ menu.d/acl.d JSON | ~24 KB |
| `apk/powerfee-chat_1.1_all.apk` | 微信查询桥接（依赖 `python3` + `powerfee`）：`/usr/bin/powerfee-chat`、`/etc/init.d/powerfee-chat`、`/etc/powerfee-chat.conf`（0600，conffile）、`/usr/share/doc/powerfee-chat/README.md` | ~25 KB |
| `ipk/powerfee_1.2.4-1_all.ipk` | 与上面主包同样的文件，opkg 格式（含 conffiles / postinst / prerm / postrm） | ~29 KB |
| `ipk/luci-app-powerfee_1.2.4-1_all.ipk` | 与上面界面包同样的文件，opkg 格式（含 postinst / postinst-pkg / prerm） | ~17 KB |
| `ipk/powerfee-chat_1.1-1_all.ipk` | 与上面桥接包同样的文件，opkg 格式（含 conffiles / postinst / prerm） | ~25 KB |

包内元数据：`name=powerfee` / `luci-app-powerfee` / `powerfee-chat`，
版本分别 `1.2.4-r1` / `1.2.4-r1` / `1.1-r1`（ipk 里写 `Version: 1.2.4-1` / `1.1-1`），
`arch=noarch`（Makefile 里写的是 `PKGARCH:=all`，apk 里显示为 noarch，ipk 里就是
`Architecture: all`），`license=MIT`，`maintainer=aodianjun`。

---

## 0. 前置条件（重要）

OpenWrt SDK 自带的“宿主工具”（`staging_dir/host/bin/*`）是 **glibc 的 x86_64 Linux 二进制**，
所以构建机必须是：

- x86_64 Linux，**glibc**（Debian/Ubuntu/Fedora…都行）；
- 不能在 Windows 上直接跑；
- **不能在 OpenWrt 自己（musl）上直接跑** —— 会报 `not found`（缺 glibc 动态链接器）。
  路由器上构建需要套一层 glibc 容器（见第 4 节，我们就是这么做的）。

宿主工具需要：`bash make gawk unzip file patch rsync zstd wget curl python3 tar`（Debian 下
`apt-get install -y --no-install-recommends make gawk unzip file patch rsync zstd wget curl ca-certificates cpio bc perl`）。

## 1. 下载 SDK（OpenWrt 25.12.2 / x86_64）

```sh
mkdir -p ~/sdk && cd ~/sdk
curl -LO https://downloads.openwrt.org/releases/25.12.2/targets/x86/64/openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64.tar.zst
# 校验（官方 sha256sums 在同一个目录）
curl -sL https://downloads.openwrt.org/releases/25.12.2/targets/x86/64/sha256sums | grep openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64.tar.zst
sha256sum openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64.tar.zst
# 解包
zstd -d -c openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64.tar.zst | tar -xf -
```

国内可以用清华镜像（同一路径，把域名换掉）：
`https://mirrors.tuna.tsinghua.edu.cn/openwrt/releases/25.12.2/targets/x86/64/...`

## 2. 一条命令构建

```sh
sh openwrt/build-apk.sh ~/sdk/openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64
```

脚本做的事：

1. 把仓库的 `files/` 同步进 `openwrt/powerfee/files/`（让包目录自包含，可以直接整体拷进 SDK）；
   把仓库的 `luci/` 同步进 `openwrt/luci-app-powerfee/luci/`；
   把 `integrations/wechat-clawbot-bridge/` 同步进 `openwrt/powerfee-chat/files/`
   （同一套树形布局：`usr/bin/powerfee-chat`、`etc/init.d/powerfee-chat`、
   `etc/powerfee-chat.conf`、`usr/share/doc/powerfee-chat/README.md`）；
2. 把 `openwrt/powerfee`、`openwrt/luci-app-powerfee`、`openwrt/powerfee-chat`
   拷进 `SDK/package/`；
3. `make defconfig` + `make -j1 package/powerfee/compile V=s`、
   `make -j1 package/powerfee-chat/compile V=s`
   （有 `luci/` 时再 `make -j1 package/luci-app-powerfee/compile V=s`）；
4. 把产物拷成 `apk/powerfee_1.2.4_all.apk`、`apk/luci-app-powerfee_1.2.4_all.apk`、
   `apk/powerfee-chat_1.1_all.apk`，并打印 sha256。

> 桥接包的源文件在 `integrations/wechat-clawbot-bridge/`（单一事实来源）；
> `openwrt/powerfee-chat/files/` 只是同步副本（供「整包拷进 SDK」用），**别手工改**。
> 直接在仓库里构建时 `Build/Prepare` 也会自动回退到 `integrations/` 取源。

> 首次构建会顺带编译 SDK 里的 toolchain 包（libc/libgcc/libstdcpp…），
> 多花一两分钟，属正常现象。

## 3. 手动构建（不用脚本）

```sh
SDK=~/sdk/openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64
cp -R openwrt/powerfee           $SDK/package/
cp -R openwrt/powerfee-chat      $SDK/package/
cp -R openwrt/luci-app-powerfee  $SDK/package/   # 只想要主包可以跳过
cd $SDK
make defconfig
make package/powerfee/compile V=s
make package/powerfee-chat/compile V=s
make package/luci-app-powerfee/compile V=s
# 产物
find $SDK/bin -name 'powerfee-*.apk'          # 注意会同时列出 powerfee-chat-*.apk
find $SDK/bin -name 'powerfee-chat-*.apk'
```

三个包都**不下载任何源码**：源文件就在包目录的 `files/`（主包、桥接包）和
`luci/`（界面）里，由 `Build/Prepare` 直接拷进 `PKG_BUILD_DIR`。所以离线也能构建。

> 直接拷 `openwrt/powerfee` / `openwrt/powerfee-chat` 进 SDK 之前，记得先跑一次
> `build-apk.sh` 把源文件同步进去（或在仓库根手动执行同步命令：主包
> `cp -R files openwrt/powerfee/files`，桥接包把 `integrations/wechat-clawbot-bridge/`
> 下的四个文件按树形布局拷进 `openwrt/powerfee-chat/files/`）；否则 Makefile 会报
> “找不到源文件目录”。

## 4. 在 OpenWrt 路由器上构建（musl 机器，套 glibc 容器）

路由器是 musl，SDK 宿主工具跑不了，用 docker 起一个 Debian 容器（我们的实际做法）：

```sh
# 1) 把 SDK 放到宿主机目录（会被挂进容器）
mkdir -p /root/sdk && cd /root/sdk
curl -LO https://mirrors.tuna.tsinghua.edu.cn/openwrt/releases/25.12.2/targets/x86/64/openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64.tar.zst

# 2) 起一个常驻构建容器（限内存，避免把路由器拖垮；这台机器没有 swap）
docker run -d --name powerfee-build --memory=512m --memory-swap=512m \
    --entrypoint /bin/sleep -v /root/sdk:/sdk -v /root/build:/build \
    debian:trixie-slim infinity

# 3) 容器里装宿主工具 + 解包 SDK
docker exec powerfee-build bash -c '
  sed -i "s|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g" /etc/apt/sources.list.d/*.sources
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    make gawk unzip file patch rsync zstd bzip2 xz-utils wget curl ca-certificates cpio bc perl
  cd /sdk && zstd -d -c openwrt-sdk-*.tar.zst | tar -xf -
'

# 4) 把仓库放进 /root/build/repo，然后构建（-j1，低配机器更稳）
docker exec powerfee-build sh -c 'cd /build/repo && sh openwrt/build-apk.sh /sdk/openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64'
```

## 5. 打 .ipk 包（opkg 系，OpenWrt ≤24.10，不需要 SDK）

```sh
sh openwrt/build-ipk.sh                 # Architecture: all（默认，推荐）
sh openwrt/build-ipk.sh --arch x86_64   # 只有确实需要写具体架构时才用
```

脚本做的事：

1. 从仓库的 `files/`、`luci/`、`integrations/wechat-clawbot-bridge/` 直接取源文件
   （与 `build-apk.sh` 同源，不手工复制内容）；
2. 从 `openwrt/powerfee/Makefile`、`openwrt/luci-app-powerfee/Makefile`、
   `openwrt/powerfee-chat/Makefile` 解析版本/发布号/许可证/维护者/依赖/描述/主页
   （单一事实来源，不会两处打架）；
3. 用内嵌的 python3 打包器生成 `ipk/powerfee_1.2.4-1_all.ipk`、
   `ipk/luci-app-powerfee_1.2.4-1_all.ipk` 与 `ipk/powerfee-chat_1.1-1_all.ipk`，
   自检结构后打印 sha256。

只依赖 POSIX `sh` + `python3`（只用标准库），**不需要 OpenWrt SDK，不需要 ar/tar/gzip**；
路由器（busybox ash）或任意 Linux/macOS 上都能跑，Windows 下要先有 POSIX sh（WSL / Git Bash）。
文件权限位是包的一部分，所以**建议在 Linux / 路由器上构建**。

### 包格式（与 OpenWrt 官方 `scripts/ipkg-build` 的输出一致）

```
gzip( tar( ./debian-binary  ./data.tar.gz  ./control.tar.gz ) )
```

* 外层是 **gzip 压缩的 tar，不是 ar**。官方源里的 .ipk 自 18.06 起就都是这个格式：
  实测从 `downloads.openwrt.org` 下载 18.06.9 / 19.07.10 / 21.02.7 / 22.03.7 / 23.05.5 /
  24.10.2 的包，全是 gzip 魔数，且 sha256 与 `Packages` 索引里的值一致；opkg 走 libarchive，
  两种容器都认。网上"ipk 就是 ar 归档"的说法来自 Debian `.deb` 的印象，照它做反而和官方产物不一致。
* 两个内层 tar 都是 GNU tar 格式：`uid/gid=0`、`uname/gname` 为空、按文件名排序、
  `mtime` 统一取 `SOURCE_DATE_EPOCH`（默认 1700000000 —— 同一份源码 + 同一个 epoch
  产出**逐字节相同**的包；想用自己的时间戳：`SOURCE_DATE_EPOCH=... sh openwrt/build-ipk.sh`）。
* `control.tar.gz`：`./control`（必需）、`./conffiles`（主包：`/etc/config/powerfee`）、
  `./postinst`、`./prerm`、`./postrm`（主包）、`./postinst-pkg`（界面包，装完清 LuCI 缓存）。
  `postinst`/`prerm` 就是官方包里的标准开头（`. /lib/functions.sh` + `default_postinst $0 $@`），
  自定义逻辑跟在后面，语义与 apk 包一致：**首次安装显式 disable + stop**（opkg 的
  `default_postinst` 会对包里的 `/etc/init.d/*` 先 enable 再 start）、把 `/etc/powerfee`
  追加进 `/etc/sysupgrade.conf`、升级（`PKG_UPGRADE=1`）不动用户状态。
* `data.tar.gz` 里的路径带前导 `./`，权限与 apk 包一致：`/usr/bin/powerfee` 0755、
  `/usr/lib/powerfee/mail.py` 0755、`/usr/lib/powerfee/wechat.py` 0755、`/etc/init.d/powerfee` 0755、
  `/etc/config/powerfee` 0600、`/www/cgi-bin/powerfee` 0755；界面包的数据文件全是 0644。

### 安装（在 opkg 系路由器上）

```sh
opkg install /tmp/powerfee_1.2.4-1_all.ipk
opkg install /tmp/luci-app-powerfee_1.2.4-1_all.ipk   # 依赖 powerfee, luci-base
opkg files powerfee                                   # 文件清单
opkg remove powerfee luci-app-powerfee                # 卸载
```

`/etc/config/powerfee` 登记为 conffile（升级不覆盖）；卸载只删包自己的文件
（`/usr/lib/powerfee`、`/www/cgi-bin/powerfee`），**保留** `/etc/powerfee`（状态与历史）
与 `/etc/config/powerfee`（用户数据）。

### 已验证到什么程度

* 结构：与官方 23.05.5 的 ipk 逐项对照（外层成员与顺序、gzip 头、control 成员与字段、
  conffiles 格式），一致；另外实测下载了 18.06.9 / 19.07.10 / 21.02.7 / 22.03.7 / 23.05.5 /
  24.10.2 的官方包，外层全是 gzip(tar)（sha256 与各自的 `Packages` 索引一致）。
* 内容：解包后与仓库 `files/`、`luci/` 逐文件 sha256 一致；权限与上表一致。
* 运行：把 ipk 解到假根、按安装路径摆放后跑 `powerfee selftest`（25/25 通过）与
  `powerfee json status`（输出合法 JSON）。
* **真 opkg 实装**（chroot）：在一台 OpenWrt 25.12 路由器上搭了个 opkg 环境 ——
  24.10.2 的 `opkg` 0.4.0 + 24.10 的 `/lib/functions.sh`、`/etc/rc.common`、`/lib/functions/*.sh`，
  依赖 jsonfilter / curl / ca-bundle / luci-base 预先登记为已安装（chroot 里没有源、
  也没有这些依赖的二进制）。然后：
  * `opkg install powerfee.ipk` → rc=0，`opkg files/status` 正常，`powerfee.conffiles`
    里记了 `/etc/config/powerfee` 的 sha256；`--force-reinstall` 升级路径 rc=0；
  * `opkg install luci-app-powerfee.ipk` → rc=0，JS/menu.d/acl.d 都落到正确路径；
  * 装完 `/etc/rc.d` 里**没有** powerfee（对照：手动 `enable` 会建 `S99powerfee` +
    `K10powerfee` 链接，`disable` 再撤掉）→ 说明 opkg 的 `default_postinst` 确实
    enable+start 了，又被我们 postinst 的 disable+stop 撤掉（装完不自动启用/启动）；
  * `opkg remove` → rc=0，包自己的文件删掉、`/etc/powerfee` 保留，
    改过的 conffile 由 opkg 自己打印 `Not deleting modified conffile /etc/config/powerfee.`；
  * 装出来的环境里 `powerfee selftest` 仍然 25/25 通过。
* `powerfee-chat` 的 ipk（2026-10-07）：结构/内容自检通过（外层 gzip(tar)、control 字段、
  data 里 4 个文件的权限与 sha256 与源一致），postinst/prerm 解包后用 `sh -n` 语法检查通过；
  **没有**做 chroot 实装（上面的 chroot 环境只搭过 powerfee/luci 两个包）。
* `powerfee-chat` 的 apk（2026-10-07）：在 25.12 真机上 `apk add` 实装过（见仓库 README /
  integrations/wechat-clawbot-bridge/README.md 的「已验证」）：三个文件就位、conf 600 且
  登记为 conffile、装完不自动启动、`enable && start` 后 running，假消息注入 → `处理: replied:brief`。
* 未验证：**没有在真正的 opkg 设备上装过**（手上这台路由器是 25.12，只有 apk 没有 opkg；
  上面是「真 opkg 二进制 + 真 opkg 脚本」的 chroot 模拟，不是真机）。

## 6. 安装 / 卸载（在路由器上，apk 系）

```sh
# 主包
apk add --allow-untrusted /tmp/powerfee_1.2.4_all.apk
# 界面包（依赖 powerfee，必须先装主包；本地 .apk 不在任何仓库里）
apk add --allow-untrusted /tmp/luci-app-powerfee_1.2.4_all.apk
# 微信查询桥接包（依赖 python3 + powerfee；装完同样不自动启用/启动）
apk add --allow-untrusted /tmp/powerfee-chat_1.1_all.apk

powerfee selftest          # 25 项判定逻辑自检
powerfee json status       # 机器可读状态（LuCI 用）
/etc/init.d/powerfee enable && /etc/init.d/powerfee start   # 装完默认不启用/不启动

apk info -L powerfee       # 文件清单
apk del powerfee luci-app-powerfee
```

安装后的路径与权限：

| 路径 | 权限 | 说明 |
|---|---|---|
| `/usr/bin/powerfee` | 0755 | 主程序（ash） |
| `/usr/lib/powerfee/mail.py` | 0755 | python3 投递助手 |
| `/usr/lib/powerfee/wechat.py` | 0755 | python3 微信助手（`powerfee wechat status/qr/enable/test`，LuCI「微信推送」页用） |
| `/etc/init.d/powerfee` | 0755 | procd 服务脚本 |
| `/etc/config/powerfee` | 0600 | UCI 配置（含邮箱授权码）。构建系统按 `INSTALL_CONF` 装成 0600，postinst 再确保一次 |
| `/etc/powerfee/` | 0700 | 运行状态与历史（postinst 创建，并追加进 `/etc/sysupgrade.conf`） |
| `/www/cgi-bin/powerfee` | 0755 | 查询端点（uhttpd CGI，默认关闭：`notify.http_enabled=0`） |
| `/www/luci-static/resources/view/powerfee/*.js` | 0644 | LuCI 视图 |
| `/usr/share/luci/menu.d/luci-app-powerfee.json` | 0644 | LuCI 菜单 |
| `/usr/share/rpcd/acl.d/luci-app-powerfee.json` | 0644 | LuCI ACL |
| `/usr/bin/powerfee-chat` | 0755 | 微信查询桥接（python3，`powerfee-chat` 包） |
| `/etc/init.d/powerfee-chat` | 0755 | 桥接的 procd 服务脚本 |
| `/etc/powerfee-chat.conf` | 0600 | 桥接配置（conffile；api_token 默认从 weclawbot 的 auth.json 读） |
| `/usr/share/doc/powerfee-chat/README.md` | 0644 | 桥接说明（= integrations/wechat-clawbot-bridge/README.md） |

包行为约定：

- **装完不自动启用/不自动启动服务**。25.12 的 `default_postinst` 会对包里的
  `/etc/init.d/*` 先 `enable` 再 `start`，所以 `Package/powerfee/postinst` 在它之后
  显式 `disable + stop` 一次（`PKG_UPGRADE=1` 的升级路径不动用户状态）。
  `powerfee-chat` 同理（并要求先配好日志源与 bot 凭据再启动）。
- `/etc/config/powerfee` 与 `/etc/powerfee-chat.conf` 在包内登记为 conffile
  （`/lib/apk/packages/<包名>.conffiles`）。卸载时若配置被改过会保留
  （apk 保留被修改的文件），未改过则删除。
  **注意 apk-tools 3 的 conffile 冲突行为**（实测）：目标文件已存在且内容与包内
  默认值不同时，apk **保留现有文件**、把包内的新版本装成 `<文件>.apk-new`
  （不是覆盖）——从手工铺的旧配置迁到包时要自己看一眼 `.apk-new` 并决定是否采纳。
- `postrm` 会删掉 `/usr/lib/powerfee`（python3 会在里面留 `__pycache__`）与
  `/www/cgi-bin/powerfee`（查询端点），但**不动** `/etc/powerfee`（用户数据）。

## 7. 坑（都是实测踩出来的）

- **路由器上的 apk 没有 `mkpkg`**：`apk mkpkg` 是 apk-tools 源码里的命令，但 OpenWrt 的
  `apk` 二进制没编译它（`ERROR: 'mkpkg' is not an apk command`）。SDK 里的
  `staging_dir/host/bin/apk` 才有 `mkpkg` —— 所以**只能用 SDK 打包**，手工 tar 打不出 ADB 容器。
- **`.apk` 不是 tar.gz**：是 apk-tools 3 的 ADB 容器（magic `ADBd`）。检查包内容用
  `apk adbdump <file>` 或 `apk extract --allow-untrusted <file>`（解到当前目录）。
- **SDK 的构建工具不能在 musl 上跑，但 `apk` 工具可以**：SDK 的 `staging_dir/host/bin/*`
  大多是 glibc 动态链接的宿主工具（make/gcc/…），在 OpenWrt 上会 `not found`，所以
  **构建**要套 glibc 容器；但 `staging_dir/host/bin/apk`（apk-tools 3.0.5）实测**能直接在
  这台 musl 路由器上跑**（`--version` / `adbdump` / `adbsign` / `mkndx` / `verify` 全可用），
  所以 `build-sign.sh` 可以直接在路由器上执行，不必进容器。
- **构建容器要限内存**：这台路由器只有 1.9 GB 内存、无 swap，还跑着别的容器；
  `--memory=512m` + `make -j1` 足够打这种纯脚本包。
- **不要 `apk upgrade`**：会动整个系统的包，跟打包无关。

ipk 侧（`build-ipk.sh` 相关）：

- **别按"ipk 是 ar 归档"来做**。Debian `.deb` 是 ar，但 OpenWrt 官方 ipk 自 18.06 起就是
  `gzip(tar(...))`（本仓库实测对照了 18.06.9 ~ 24.10.2 的官方包，sha256 与索引一致）。
  按 ar 做出来的包虽然"看起来像 ipk"，但和官方产物不一致 —— 没必要冒这个险。
- **opkg 解包时要调用外部 `gzip`**：opkg（opkg-lede）读 ipk 的方式是把整个文件喂给
  `gzip -d -c`（`libbb/gzip.c` 里的 `execlp("gzip", "gzip", "-d", "-c", ...)`），
  再用它自己的 tar 解析器走外层容器、并对 `control.tar.gz`/`data.tar.gz` 再调一次 gzip。
  所以设备上**必须有 gzip**（OpenWrt 的 busybox 自带；做 chroot/容器验证时容易漏掉，
  漏了会报 `pkg_init_from_file: Malformed package file`，看着像包坏了，其实是环境缺 gzip）。
- **在路由器 /tmp 里做 chroot 验证时**：`/tmp` 是 nodev 的 tmpfs，`mknod` 出来的
  `/dev/null` 打不开（EACCES），会让脚本里所有 `2>/dev/null` 静默失败；用可写的普通文件代替。
  另外 chroot 里的命令要 `</dev/null`，否则子进程等 stdin 会把验证挂住。
