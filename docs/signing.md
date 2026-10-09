# 包签名：官方签名拿不到，自签名怎么做（OpenWrt 25.x / apk-tools 3）

本文讲清三件事：**OpenWrt 25.x 的 .apk 到底怎么签**（附实测证据）、**为什么第三方拿不到官方签名**、
以及**自建包的自签名完整做法**（本项目已提供脚本与产物，真机验证过）。

一句话结论：

- 官方签名拿不到 —— 官方包/索引是 OpenWrt buildbot 的私钥签的，私钥不对外，第三方无法让 OpenWrt 代签；
  除非包被官方 feed（openwrt/packages、openwrt/luci 等）收录并进入官方构建。
- 自建包的正解 = **自己生成密钥 → 用自己的私钥签名 → 把公钥装到目标设备的 `/etc/apk/keys/`**。
  之后 `apk add` 正常校验，不需要 `--allow-untrusted`。
- 本项目已经这么做了：`openwrt/build-sign.sh` 负责签名，`signing/` 目录放公钥和已签名的包/索引。
  真机上验证过「装公钥 → `apk del` → 不加 `--allow-untrusted` 直接 `apk add`」成功。

---

## 一、apk-tools 3 的签名机制（实测，非推测）

测试环境：OpenWrt 25.12.2 (x86_64)，`apk --version` = **apk-tools 3.0.5**。
证据来源：SDK 宿主工具、apk-tools 3.0.5 源码、官方 feed 的真实包与索引。

### 1.1 文件格式

OpenWrt 25.x 的 `.apk` 和仓库索引 `packages.adb` 都是 **ADB v3 容器**，不是 v2 的 tar.gz：

- 文件开头 4 字节是容器魔数：`ADBd` = 原始 deflate 压缩、`ADB.` = 未压缩、`ADBc` = 指定算法/级别
  （源码 `src/adb_comp.c`；实测官方 `packages.adb`、官方包、自建包全是 `ADBd`）。
- 解压后：文件头 `magic "ADB." + 4 字节 schema`（包 = `pckg`，索引 = `indx`），随后是块序列：

  | 块类型 | 含义 | 说明 |
  |---|---|---|
  | 0 | ADB（元数据） | 包/索引的元数据本体，签名对象 |
  | 1 | SIG（签名） | 签名块，见下 |
  | 2 | DATA（文件数据） | 包内每个文件的压缩数据块 |
  | 3 | EXT | 扩展块 |

- 签名块内容（源码 `struct adb_sign_v0`，实测字段值一致）：
  `sign_ver=0` + `hash_alg=4`（SHA-512）+ **16 字节 keyid** + DER 编码的 ECDSA 签名（71 字节，P-256）。
- 签名对象：元数据块（类型 0）的 SHA-512，混入 schema 后由私钥签名（`adb_digest_v0_signature`）。
- keyid 算法（源码 `crypto_openssl.c: apk_pkey_init`，实测核对）：
  `keyid = SHA-512(公钥 EC point 04||X||Y)[0:16]`。
  用官方公钥 `openwrt-25.12.pem` 算出的 keyid 与官方索引签名块里的
  `026c4bef63b5dcffaf46115a5f610f76` 完全一致。

### 1.2 信任模型：官方只签索引，包靠索引里的哈希背书

从官方 feed（TUNA 镜像）下载实测：

- **官方 `packages.adb` 有签名块**：`apk verify` → `OK`，SIG 块 keyid = `026c4bef...`（对应官方公钥）。
- **官方包没有签名块**：以 `msmtp-1.8.32-r1.apk` 为例，解析容器只有 `ADB 元数据 + DATA` 块，没有 SIG；
  `apk verify msmtp-1.8.32-r1.apk` → `UNTRUSTED signature`（即使 `/etc/apk/keys/` 里装着官方公钥）。

所以官方模型是：**签索引**。`apk` 从源安装时先验证索引签名（可信），再用索引里每个包的 `hashes`（SHA-256）
和包内每个文件的哈希校验内容。这也是为什么单独拿一个官方包文件来 `apk verify` 会说不可信 ——
包的信任来自「签过名的索引为它背书」，而不是包自己的签名。

### 1.3 密钥格式与位置

- 密钥：**EC P-256（prime256v1）的 PEM**，私钥 `-----BEGIN EC PRIVATE KEY-----`，公钥 `-----BEGIN PUBLIC KEY-----`。
  （apk-tools 3 也支持 RSA；OpenWrt 官方和本项目的做法都是 P-256。）
- 公钥位置：`/etc/apk/keys/*.pem`（官方是 `openwrt-25.12.pem`，178 字节）。
- **文件名不重要**：`apk-keys(5)` 原文 —— "The APKv2 packages require the filename of public key to match the
  signing key name in the package. **APKv3 files are matched using the public key identity and filename is not
  significant.**"（v3 用 keyid 匹配，随便起名。）

### 1.4 用什么工具签

| 工具 | 用途 | 在哪 |
|---|---|---|
| `apk adbsign --sign-key <私钥> <文件...>` | 给已构建好的包/索引**补签名**（会重新压缩） | SDK 宿主工具 `staging_dir/host/bin/apk` |
| `apk mkndx --sign-key <私钥> -o packages.adb *.apk` | 生成并签名**仓库索引** | 同上 |
| `apk mkpkg --sign-key <私钥> ...` | 建包时直接签 | 同上 |
| `openssl ecparam -name prime256v1 -genkey -noout -out key.pem` | 生成密钥 | SDK 宿主 openssl 或系统 openssl |

⚠️ **目标设备自带的 `apk` 是精简版**（`apk --help` 里没有 `adbsign`/`mkndx`/`mkpkg`），
签名必须用 SDK（或完整构建环境）里的宿主 apk。本项目在路由器上的 SDK 路径：
`/root/sdk/openwrt-sdk-25.12.2-.../staging_dir/host/bin/apk`。

两个实测行为细节（apk-tools 3.0.5，脚本里已处理）：

1. **读取未签名的输入需要 `--allow-untrusted`**：数据块（DATA）在读取侧就要求「已信任」，
   所以给一个未签名的包补签名时，命令要写成
   `apk --allow-untrusted adbsign --sign-key key.pem pkg.apk`；`mkndx` 同理。
2. **一次 `adbsign` 调用只给第一个文件写签名**（内部 `signatures_written` 只置位一次），
   所以要对每个包分别调用一次 —— `build-sign.sh` 就是这么做的。

---

## 二、官方签名为什么拿不到

- OpenWrt 官方构建（buildbot）用自己的私钥（对应 `/etc/apk/keys/openwrt-25.12.pem`）签**索引**，
  私钥只在官方基础设施里，不对外发布、也不提供代签服务。
- 第三方作者唯一能拿到官方签名的路径：**包被官方 feed 收录**（`openwrt/packages`、`openwrt/luci` 等），
  由官方构建流程产出并签名。这需要走上游 PR 流程、符合收录标准（通用性、许可证、维护者等），
  不适合个人小工具。
- 结论：**不要指望官方签名**；自建包就自签，或者用自建签名仓库。

---

## 三、作者侧：自签名完整流程（本项目）

### 3.1 一次性：生成密钥对

私钥**绝不能进仓库**（`.gitignore` 已加防线，`build-sign.sh` 也会拒绝把私钥写进仓库目录）。
建议放在构建机的仓库外，例如 `~/.powerfee-signing/powerfee-signing.key`：

```sh
mkdir -p ~/.powerfee-signing
openssl ecparam -name prime256v1 -genkey -noout -out ~/.powerfee-signing/powerfee-signing.key
chmod 600 ~/.powerfee-signing/powerfee-signing.key
openssl ec -in ~/.powerfee-signing/powerfee-signing.key -pubout -out ~/.powerfee-signing/powerfee-signing.pem
```

（路由器上跑的话，openssl 可以用 SDK 的：`/root/sdk/*/staging_dir/host/bin/openssl`。
本项目的私钥就放在构建机 `/root/pf_signing/`，公钥已随仓库发布为 `signing/powerfee-signing.pem`。）

**换密钥 = 所有已装公钥的设备都要重新装公钥**，所以生成一次就好好保管。

### 3.2 每次发版：一条命令

```sh
# 先按原有流程构建未签名包（openwrt/build-apk.sh），然后：
sh openwrt/build-sign.sh --key ~/.powerfee-signing/powerfee-signing.key
```

脚本做的事（每一步都可以手工复现）：

1. 从私钥导出公钥到 `signing/powerfee-signing.pem`；
2. 对 `apk/` 下每个 `.apk`：复制到 `signing/repo/<name>-<version>.apk`，
   然后 `apk --allow-untrusted adbsign --sign-key <私钥> <包>`，并自检 `apk verify`；
3. 在 `signing/repo/` 里生成签名索引：`apk --allow-untrusted mkndx --sign-key <私钥> -o packages.adb *.apk`；
4. 打印所有产物的 sha256。

产物（`signing/repo/`，可整体发布）：

```
powerfee-1.0.3-r1.apk             已签名，可直接 apk add
luci-app-powerfee-1.0.3-r1.apk    已签名
packages.adb                      已签名的仓库索引（自建源用）
```

注意：**签名会重新压缩**，签过的包与 `apk/` 里的原始包字节不同（内容和版本不变），这是正常的。
而且 ECDSA 签名带随机数，**同一份输入重复签名也会得到不同的字节**（大小可能差几个字节）——
所以签名产物不要用 sha256 去和上次比对，认版本号即可。
另外 `signing/repo/` 里的包名必须是 `<name>-<version>.apk`（apk 从源下载时按这个规则拼 URL）。

---

## 四、用户侧：两种用法

### 4.1 直接装签名包（最简单）

```sh
# 1) 把公钥装进信任目录（文件名任意，v3 按 keyid 匹配）
#    最稳的办法是和传包一样用 scp 传上去：
scp -O signing/powerfee-signing.pem root@192.168.1.1:/etc/apk/keys/
#    也可以让路由器自己下载，但注意 25.x 自带的 wget 不带 TLS（wget-nossl）：
#    https 会报 "wget: exited with error 1"，要么先 apk add wget-ssl，
#    要么用 uclient-fetch，要么走 http（公钥本身不敏感，但建议校验指纹）

# 2) 安装签名包 —— 不需要 --allow-untrusted
apk add /tmp/powerfee-1.0.3-r1.apk
apk add /tmp/luci-app-powerfee-1.0.3-r1.apk

# 验证签名（可选）
apk verify /tmp/powerfee-1.0.3-r1.apk      # -> OK
```

> 公钥文件本身可以公开；它的 SHA-256 指纹是
> `b0baf0bc7e3e76ab09ca3dc75d1c657109479fff8d22e62ce58c3c4ec8ef3e63`（P-256，178 字节），
> 拿到手可以核对一下再放进 `/etc/apk/keys/`。

### 4.2 自建签名仓库（多设备 / 长期用，推荐）

把 `signing/repo/` 整个目录发布出去（HTTP 静态目录、内网服务器、对象存储都行），
在设备上加一行源：

```sh
echo 'http://<你的服务器>/powerfee/packages.adb' > /etc/apk/repositories.d/powerfee.list
apk update
apk add powerfee luci-app-powerfee        # 从自建源安装/升级，无需 --allow-untrusted
```

两个坑：

- **OpenWrt 25.x 自带的 wget 不带 TLS（wget-nossl）**，`https://` 源会报 `wget: exited with error 1`；
  要么用 `http://`（索引有签名，防篡改），要么先 `apk add wget-ssl`。
- 源里包文件名必须是 `<name>-<version>.apk`，索引和包要放在同一目录（或按索引里的相对路径摆放）。

---

## 五、真机验证记录（2026-10-07，OpenWrt 25.12.2 x86_64）

> 这一节是 **1.0.1 时期**的原始输出，按当时的样子保留（命令与输出原文照录）。
> 1.0.2 只加了 LuCI「微信推送」页和 `powerfee wechat` 助手，签名/安装流程完全没变 ——
> 把下文的包名换成 `-1.0.3-` 即可，步骤一一对应。

在装过本项目包的实机上做了完整往返验证。关键步骤与原始输出（为便于阅读，文件路径做了简化，
命令与输出原文照录；签名前后各做一次卸载/重装，配置 md5 全程未变）：

```
# 1) 装公钥
root@OpenWrt:~# cp powerfee-signing.pem /etc/apk/keys/
root@OpenWrt:~# ls -la /etc/apk/keys/
-rw-r--r-- 1 root root 178 openwrt-25.12.pem
-rw-r--r-- 1 root root 178 powerfee-signing.pem

# 2) 卸载（配置 /etc/config/powerfee 与 /etc/powerfee/ 保留）
root@OpenWrt:~# apk del powerfee luci-app-powerfee
(1/2) Purging luci-app-powerfee (1.0.1-r1)
(2/2) Purging powerfee (1.0.1-r1)
  Executing powerfee-1.0.1-r1.pre-deinstall
  Executing powerfee-1.0.1-r1.post-deinstall
OK: 466.7 MiB in 426 packages

# 3) 不加 --allow-untrusted，直接装签名包（这是关键一步）
root@OpenWrt:~# apk add /tmp/powerfee-1.0.1-r1.apk
(1/1) Installing powerfee (1.0.1-r1)
  Installing file to etc/config/powerfee.apk-new
  Executing powerfee-1.0.1-r1.post-install
OK: 466.7 MiB in 427 packages
# ↑ 没有任何 UNTRUSTED / 没有要求 --allow-untrusted

root@OpenWrt:~# apk verify /tmp/powerfee-1.0.1-r1.apk
/tmp/powerfee-1.0.1-r1.apk: OK

# 4) 自建源路线
root@OpenWrt:~# echo '/tmp/repo/packages.adb' > /etc/apk/repositories.d/powerfee-test.list
root@OpenWrt:~# apk update
 [/tmp/repo/packages.adb]
OK: 11439 distinct packages available
root@OpenWrt:~# apk add powerfee luci-app-powerfee
(1/2) Installing powerfee (1.0.1-r1)
(2/2) Installing luci-app-powerfee (1.0.1-r1)
OK: 466.8 MiB in 428 packages

# 5) 冒烟
root@OpenWrt:~# powerfee selftest | tail -1
全部通过（25 项）
root@OpenWrt:~# /etc/init.d/powerfee status
running
# 配置 md5 往返前后一致；房间搜索正常；服务已 enable + running
```

对照实验（同一台机器，用**未签名**的原始包）：

```
root@OpenWrt:~# apk verify /tmp/powerfee_1.0.1_all.apk
/tmp/powerfee_1.0.1_all.apk: UNTRUSTED signature
```

即：同一个包，签名前不可信（要 `--allow-untrusted`），签名 + 装公钥后直接通过。

---

## 六、边界与注意事项（没做到 / 别踩）

- **OpenWrt 官方签名无法获取**：除非包被官方 feed 收录并进入官方构建，否则拿不到。
  这不是技术问题，是信任归属问题 —— 官方不会给第三方内容背书的签名。
- **OpenWrt 官方自己不签单个包**（只签索引）。apk-tools 3 支持包级签名（本文验证可用），
  但如果目标是「和官方生态一致」，自建仓库 + 签名索引才是主推方式；包级签名适合
  「下载一个 .apk 手动安装」这种分发方式。
- `signing/repo/packages.adb` 是**当前版本**的索引；发新版后要重跑 `build-sign.sh` 重新生成，
  否则索引里的哈希对不上新包（apk 会拒绝）。
- 私钥丢失 = 无法再给同一个公钥签名（只能换密钥并让用户重装公钥）；私钥泄露 = 别人可以冒名发包。
  备份私钥，但**永远不要提交进 Git / 不要放进公开仓库**。
- 签名只证明「包来自私钥持有者、内容没被改过」，不证明代码质量。装第三方公钥前请自己确认来源。
