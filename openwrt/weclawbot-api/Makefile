#
# ClawBot(微信) 推送服务 · OpenWrt 原生包 —— weclawbot-api
#
# 本包不编译任何源码：把上游发布的二进制 + procd init 脚本 + UCI 配置 + LICENSE
# 打包成 OpenWrt 安装包（apk/ipk 由 SDK 生成）。
#
# 上游项目：https://github.com/Cp0204/WeClawBot-API  （MIT 许可，版权归原作者）
# 上游 release：v1.0；上游 Docker 镜像：cp0204/weclawbot-api
#
# ===========================================================================
# ⚠️ 二进制来源（重要）：
#
#   两个架构都从「本仓库自己的 release 资产」下载（不把二进制塞进仓库树，
#   免得仓库无谓膨胀 9.6MB；release 资产是流式直传，比 git 大 blob 稳得多）。
#   本仓库的 release 地址见下面 WCB_RELEASE_URL；若将来换版本，改一处即可。
#
#   x86_64  ：weclawbot-api_linux_amd64 —— 取自上游官方 Docker 镜像
#             （dockerproxy.net/cp0204/weclawbot-api
#               @sha256:89e1f24346627e2654a75cc7744669bc0fffa82ab2e1ea9c14ecf14dfce333f4，
#               构建于 2026-03-22，与 v1.0 release 同日），纯静态链接，
#               已在本路由器（OpenWrt 25.12.2 x86_64 / musl）实测可运行，
#               也正是迁移前容器里跑的那份，行为完全一致；
#               该文件由本仓库 release 原样转发，sha256 见 WCB_BIN_SHA256。
#             ⚠️ 不用 GitHub release v1.0 的 amd64 资产：实测那份是 glibc 动态
#                链接（PT_INTERP=/lib64/ld-linux-x86-64.so.2，DT_NEEDED libc.so.6），
#                在 musl 上跑不起来（rc=127 / "Error relocating ... symbol not found"），
#                且 OpenWrt 25.12 源里没有 gcompat 可救。
#                该资产 sha256（仅作记录，本包不用）：
#                  b467cd3402397c78faed4dcbbe2031e8b6ade89968d6c7b254f76d4286e7c48d
#
#   aarch64 ：weclawbot-api_linux_arm64 —— 直接取上游 release v1.0
#             （该资产实测为纯静态 ELF：无 PT_INTERP / 无 PT_DYNAMIC），
#             上游直连不稳，附了两个公共镜像，下载器按顺序尝试。
#
#   两者都会：下载器按 PKG_HASH 校验 + Build/Prepare 里再校验一次 sha256。
#
#   其它架构：上游没有对应二进制（没有 armv7 / 32 位 x86 / mips …），
#             本包明确 warning 并跳过，不产生任何安装包（绝不静默出跑不了的包）。
# ===========================================================================
#
# 在 OpenWrt SDK 里构建：
#   cp -R <仓库>/openwrt/weclawbot-api <SDK>/package/
#   cd <SDK> && make package/weclawbot-api/compile V=s
#   （两个架构都需要能访问对应下载源之一；离线构建可预先把二进制放进 dl/）
#
include $(TOPDIR)/rules.mk

PKG_NAME:=weclawbot-api
PKG_VERSION:=1.0
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_LICENSE_FILES:=LICENSE
PKG_MAINTAINER:=aodianjun

# ---------------------------------------------------------------------------
# 架构判断
#
# 注意 OpenWrt 的一个坑：SDK 扫描包元数据时（scan.mk 以 DUMP=1 调本 Makefile）
# 不加载 .config，$(ARCH)/$(ARCH_PACKAGES) 都是空的。因此分两种情形：
#   * 构建阶段（DUMP 未设，.config 已加载）：按真实目标架构决定二进制来源，
#     不支持的架构直接跳过不构建（下面有明确 warning）；
#   * 元数据扫描阶段（DUMP=1）：按 amd64 记录元数据，只为让包出现在
#     menuconfig/依赖解析里——实际安装的二进制仍由构建阶段决定。
# 架构取值：x86/64 上是 "x86_64"；aarch64 目标上是 "aarch64_generic"、
# "aarch64_cortex-a53" 等（所以用 aarch64% 前缀匹配）。
# ---------------------------------------------------------------------------
ifeq ($(DUMP),)
  WCB_ARCH:=$(strip $(if $(ARCH),$(ARCH),$(ARCH_PACKAGES)))
else
  WCB_ARCH:=x86_64
endif

# 本仓库自己的 release（x86_64 的二进制由它转发；换版本只改这一行）
WCB_RELEASE_TAG:=v1.2.0
WCB_RELEASE_URL:=https://github.com/aodianjun/PowerFeeWRT/releases/download/$(WCB_RELEASE_TAG)

ifneq ($(filter x86_64,$(WCB_ARCH)),)
  WCB_ARCH_TAG:=amd64
  WCB_BIN_SHA256:=d974214767d1cc6b5dce97755a2fd4be661b4f2506ae72c9504755785ae388b0
  PKG_SOURCE:=weclawbot-api_linux_amd64
  PKG_SOURCE_URL:= \
	$(WCB_RELEASE_URL) \
	https://ghproxy.net/$(WCB_RELEASE_URL) \
	https://gh-proxy.com/$(WCB_RELEASE_URL)
  PKG_HASH:=$(WCB_BIN_SHA256)
else ifneq ($(filter aarch64%,$(WCB_ARCH)),)
  WCB_ARCH_TAG:=arm64
  WCB_BIN_SHA256:=4201222d0d760573a3db4be0f5953423d67c5529f1b5d62d00d54f52e4b24351
  PKG_SOURCE:=weclawbot-api_linux_arm64
  PKG_SOURCE_URL:= \
	https://github.com/Cp0204/WeClawBot-API/releases/download/v$(PKG_VERSION) \
	https://ghproxy.net/https://github.com/Cp0204/WeClawBot-API/releases/download/v$(PKG_VERSION) \
	https://gh-proxy.com/https://github.com/Cp0204/WeClawBot-API/releases/download/v$(PKG_VERSION)
  PKG_HASH:=$(WCB_BIN_SHA256)
else
  WCB_ARCH_TAG:=
endif

PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

# 二进制来源：两个架构都从下载缓存取（x86_64 来自本仓库 release，aarch64 来自上游 release）
WCB_BIN_SRC:=$(DL_DIR)/$(PKG_SOURCE)

include $(INCLUDE_DIR)/package.mk

ifeq ($(WCB_ARCH_TAG),)
  $(warning weclawbot-api: 不支持的架构 "$(WCB_ARCH)"（ARCH="$(ARCH)" ARCH_PACKAGES="$(ARCH_PACKAGES)"）—— 上游 Cp0204/WeClawBot-API 没有 armv7/32 位 x86/mips 等架构的二进制，本包在此架构上不构建，不会产生安装包。)
else

define Package/weclawbot-api
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=WeChat ClawBot push API server (upstream prebuilt binary wrapper)
  URL:=https://github.com/Cp0204/WeClawBot-API
  DEPENDS:=+ca-bundle
endef

define Package/weclawbot-api/description
  微信 ClawBot 推送服务（weclawbot-api）——第三方项目 Cp0204/WeClawBot-API
  的官方二进制 + init/UCI 集成的 OpenWrt 封装包（MIT 许可，版权归原作者，
  许可证全文见 /usr/share/licenses/weclawbot-api/LICENSE）。

  上游地址：https://github.com/Cp0204/WeClawBot-API
  本包不含任何代码修改，只做打包：装到 /usr/bin/weclawbot-api，配 procd
  init 脚本与 UCI 配置，二进制按 sha256 校验。

  二进制来源：
    x86_64  —— 上游官方 Docker 镜像（cp0204/weclawbot-api）里的静态二进制，
               由本仓库 release 资产转发（GitHub release 的 amd64 资产是
               glibc 动态链接，musl 上跑不起来）；
    aarch64 —— 上游 GitHub release v1.0 的 linux_arm64 静态资产（sha256 校验）；
    其它架构（armv7/32 位 x86/mips 等）上游无二进制，本包不构建。

  使用：
    1) 扫码绑定机器人（凭据写入 /etc/weclawbot/config/auth.json，权限 600）：
         cd /etc/weclawbot && weclawbot-api bot
       控制台里 /login 扫码、/bots 查看已绑定机器人。
    2) 启动服务（端口默认 26322，改 weclawbot-api.main.port）：
         uci set weclawbot-api.main.enabled=1 && uci commit weclawbot-api
         /etc/init.d/weclawbot-api enable && /etc/init.d/weclawbot-api start
    3) 推送消息（<botid>/<token> 见 auth.json）：
         curl -X POST -H "Authorization: Bearer <token>" \
              -H 'Content-Type: application/json' \
              -d '{"text":"你好"}' \
              http://127.0.0.1:26322/bots/<botid>/messages

  /etc/weclawbot/（凭据与配置）已加入 sysupgrade 保留列表，升级固件不丢。
endef

define Build/Prepare
	rm -rf $(PKG_BUILD_DIR)
	mkdir -p $(PKG_BUILD_DIR)
	# files/：init 脚本 / UCI 默认配置 / 上游 LICENSE（自包含，可直接拷进 SDK）
	$(CP) $(CURDIR)/files/. $(PKG_BUILD_DIR)/
	# 上游二进制（两个架构都来自 dl/ 下载缓存，已由下载器按 PKG_HASH 校验）
	$(CP) $(WCB_BIN_SRC) $(PKG_BUILD_DIR)/weclawbot-api
	chmod 0755 $(PKG_BUILD_DIR)/weclawbot-api
	# 二进制 sha256 校验（不通过就直接失败，绝不静默装错文件）。
	# 注意：这里刻意不用 shell 的 $$(...) 取哈希——本 SDK 里 Build/Prepare 的文本
	# 会被多展开一层（Build/CoreTargets 在 eval 里），shell 里的 $ 得写 $$$$，
	# 容易踩坑；用 sha256sum -c 的输入流形式最稳（coreutils 是 OpenWrt 构建主机的
	# 硬性前置依赖，prereq-build.mk 里有检查）。
	@echo "$(WCB_BIN_SHA256)  $(PKG_BUILD_DIR)/weclawbot-api" | sha256sum -c - >/dev/null \
		|| { echo "ERROR: weclawbot-api 二进制 sha256 校验失败（期望 $(WCB_BIN_SHA256)）"; false; }
endef

define Build/Compile
endef

define Package/weclawbot-api/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/weclawbot-api $(1)/usr/bin/weclawbot-api
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/etc/init.d/weclawbot-api $(1)/etc/init.d/weclawbot-api
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) $(PKG_BUILD_DIR)/etc/config/weclawbot-api $(1)/etc/config/weclawbot-api
	$(INSTALL_DIR) $(1)/usr/share/licenses/weclawbot-api
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/LICENSE $(1)/usr/share/licenses/weclawbot-api/LICENSE
endef

# 配置文件（升级时按 conffile 处理）
define Package/weclawbot-api/conffiles
/etc/config/weclawbot-api
endef

# 安装后置脚本（本段在 default_postinst 之后执行，$${IPKG_INSTROOT} 非空 = 构建固件镜像）。
# 凭据目录 /etc/weclawbot/config 预置为 0700；已有 auth.json 时收紧到 600；
# 并把 /etc/weclawbot 加入 sysupgrade 保留列表（扫码凭据升级固件不丢）。
# 服务启用/启动交给 default_postinst（装完即 enable+start）。
define Package/weclawbot-api/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	mkdir -p /etc/weclawbot/config
	chmod 700 /etc/weclawbot /etc/weclawbot/config
	if [ -f /etc/weclawbot/config/auth.json ]; then
		chmod 600 /etc/weclawbot/config/auth.json
	fi
	grep -q '^/etc/weclawbot$$' /etc/sysupgrade.conf 2>/dev/null || \
		echo '/etc/weclawbot' >> /etc/sysupgrade.conf
}
endef

$(eval $(call BuildPackage,weclawbot-api))

endif
