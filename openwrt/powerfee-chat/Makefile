#
# powerfee-chat · OpenWrt 包 —— 微信 ClawBot 电量查询桥接
#
# 纯 python3 脚本包（只用标准库），与 CPU 架构无关，因此 PKGARCH:=all，
# 产物是 powerfee-chat-1.1-r1.apk（架构 all）。
#
# 源文件（单一事实来源）：仓库的 integrations/wechat-clawbot-bridge/
#   powerfee-chat.py           -> /usr/bin/powerfee-chat                  (0755)
#   powerfee-chat.init         -> /etc/init.d/powerfee-chat               (0755)
#   powerfee-chat.conf.example -> /etc/powerfee-chat.conf                 (0600, conffile)
#   README.md                  -> /usr/share/doc/powerfee-chat/README.md  (0644)
#
# 取源的两种方式（Build/Prepare 自动识别，都归一化成上面的安装树）：
#   1) 包目录自带的 files/（整包拷进 SDK 也能构建；由 openwrt/build-apk.sh
#      从 integrations/wechat-clawbot-bridge/ 同步生成，**别手工改 files/ 里的副本**，
#      改了会被下次同步覆盖，且与单一事实来源不一致）；
#   2) 直接读仓库的 ../../integrations/wechat-clawbot-bridge/（在仓库里原地构建时用）。
#
# 在 OpenWrt SDK 里构建：
#   cp -R <仓库>/openwrt/powerfee-chat <SDK>/package/
#   cd <SDK> && make package/powerfee-chat/compile V=s
# 或者直接跑 <仓库>/openwrt/build-apk.sh <SDK 目录>（推荐，会顺带同步源码）。
#
include $(TOPDIR)/rules.mk

PKG_NAME:=powerfee-chat
PKG_VERSION:=1.1
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_MAINTAINER:=aodianjun

PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

# 源文件目录：优先用包目录自带的 files/（自包含，可直接拷进 SDK）；
# 不存在时回退到仓库的 integrations/wechat-clawbot-bridge/（原地构建时用）。
POWERFEE_CHAT_SRC:=$(firstword $(wildcard $(CURDIR)/files) $(wildcard $(CURDIR)/../../integrations/wechat-clawbot-bridge))
ifeq ($(POWERFEE_CHAT_SRC),)
$(error 找不到源文件目录（$(CURDIR)/files 或 $(CURDIR)/../../integrations/wechat-clawbot-bridge），请先运行 openwrt/build-apk.sh 同步源码)
endif

include $(INCLUDE_DIR)/package.mk

define Package/powerfee-chat
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=WeChat ClawBot bridge for powerfee (query electricity balance by chat)
  URL:=https://github.com/aodianjun/PowerFeeWRT
  DEPENDS:=+python3 +powerfee
  PKGARCH:=all
endef

define Package/powerfee-chat/description
  微信 ClawBot 电量查询桥接：常驻一个 python3 小进程（只用标准库），盯着
  weclawbot-api 的日志（logread / logfile / docker logs 三种来源），识别
  「电量 / 电费 / 余额」等关键词，调用 powerfee 查询余额，再通过 ClawBot 的
  HTTP API 把结果发回微信。即：在微信里给 ClawBot 发「电量」，收到当前余额。

  依赖 python3（运行桥接本身）与 powerfee（提供 json brief/status 子命令）。

  配置 /etc/powerfee-chat.conf（权限 600，安装时登记为 conffile）；
  api_token 默认从 /etc/weclawbot/config/auth.json 读取，不必写进配置。

  安装后不会自动启用/启动服务。先按 /usr/share/doc/powerfee-chat/README.md
  核对日志源与 bot 凭据（python3 /usr/bin/powerfee-chat --check-config），再：
    /etc/init.d/powerfee-chat enable && /etc/init.d/powerfee-chat start
  离线自检（41 项断言，不打扰微信）：python3 /usr/bin/powerfee-chat --selftest
endef

define Build/Prepare
	rm -rf $(PKG_BUILD_DIR)
	mkdir -p $(PKG_BUILD_DIR)/usr/bin $(PKG_BUILD_DIR)/etc/init.d \
		$(PKG_BUILD_DIR)/usr/share/doc/powerfee-chat
	if [ -f $(POWERFEE_CHAT_SRC)/usr/bin/powerfee-chat ]; then \
		$(CP) $(POWERFEE_CHAT_SRC)/. $(PKG_BUILD_DIR)/; \
	else \
		$(CP) $(POWERFEE_CHAT_SRC)/powerfee-chat.py $(PKG_BUILD_DIR)/usr/bin/powerfee-chat; \
		$(CP) $(POWERFEE_CHAT_SRC)/powerfee-chat.init $(PKG_BUILD_DIR)/etc/init.d/powerfee-chat; \
		$(CP) $(POWERFEE_CHAT_SRC)/powerfee-chat.conf.example $(PKG_BUILD_DIR)/etc/powerfee-chat.conf; \
		$(CP) $(POWERFEE_CHAT_SRC)/README.md $(PKG_BUILD_DIR)/usr/share/doc/powerfee-chat/README.md; \
	fi
endef

define Build/Compile
endef

define Package/powerfee-chat/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/usr/bin/powerfee-chat $(1)/usr/bin/powerfee-chat
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/etc/init.d/powerfee-chat $(1)/etc/init.d/powerfee-chat
	$(INSTALL_DIR) $(1)/etc
	$(INSTALL_CONF) $(PKG_BUILD_DIR)/etc/powerfee-chat.conf $(1)/etc/powerfee-chat.conf
	$(INSTALL_DIR) $(1)/usr/share/doc/powerfee-chat
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/usr/share/doc/powerfee-chat/README.md $(1)/usr/share/doc/powerfee-chat/README.md
endef

# 配置文件（升级时按 conffile 处理：写进 /lib/apk/packages/powerfee-chat.conffiles）
define Package/powerfee-chat/conffiles
/etc/powerfee-chat.conf
endef

# 安装后置脚本。构建系统生成的 post-install 结构是
#   ... default_postinst <本段代码>
# 即本段代码在 default_postinst 之后执行（已用 apk adbdump 核对过 powerfee 产物）。
# 注意 25.12 的 default_postinst 对包里 /etc/init.d/* 的每个脚本都会
# 先 enable 再 start，而本包要求「装完不自动启用/启动服务」（先配好日志源与
# bot 凭据再启动），所以首次安装时显式 disable + stop 一次（升级时不动用户
# 当前的启用/运行状态）。
# （IPKG_INSTROOT 非空 = 正在构建固件镜像，此时什么都不做，交给默认流程。）
define Package/powerfee-chat/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	[ -f /etc/powerfee-chat.conf ] && chmod 600 /etc/powerfee-chat.conf
	echo "powerfee-chat: 首次使用请按 /usr/share/doc/powerfee-chat/README.md 配置日志源与 bot 凭据（/etc/powerfee-chat.conf）"
	[ "$${PKG_UPGRADE}" = "1" ] || {
		/etc/init.d/powerfee-chat disable >/dev/null 2>&1
		/etc/init.d/powerfee-chat stop >/dev/null 2>&1
	}
}
endef

# 卸载后置脚本：本包没有自己生成的运行文件（日志走 syslog、无状态文件），
# 无需额外清理；/etc/powerfee-chat.conf 是 conffile，交给包管理器处理
# （改过的保留，未改的删除）。
define Package/powerfee-chat/postrm
#!/bin/sh
:
endef

$(eval $(call BuildPackage,powerfee-chat))
