#
# 宿舍电量哨兵 · OpenWrt 版 —— 主程序包
#
# 纯脚本包（busybox ash + 可选 python3 / msmtp 投递），与 CPU 架构无关，
# 因此 PKGARCH:=all，产物是 powerfee-1.2.4-r1.apk（架构 all）。
#
# 在 OpenWrt SDK 里构建：
#   cp -R <仓库>/openwrt/powerfee <SDK>/package/
#   cd <SDK> && make package/powerfee/compile V=s
# 或者直接跑 <仓库>/openwrt/build-apk.sh <SDK 目录>（推荐，会顺带同步源码）。
#
# 本包不下载任何源码：源文件就在包目录的 files/ 下（由 build-apk.sh 从
# 仓库根的 files/ 同步而来），Build/Prepare 直接拷贝。
#
include $(TOPDIR)/rules.mk

PKG_NAME:=powerfee
PKG_VERSION:=1.2.4
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_MAINTAINER:=aodianjun

PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

# 源文件目录：优先用包目录自带的 files/（这样整个包目录可以直接拷进 SDK）；
# 不存在时回退到仓库根的 files/（在仓库里原地构建时用）。
POWERFEE_FILES:=$(firstword $(wildcard $(CURDIR)/files) $(wildcard $(CURDIR)/../../files))
ifeq ($(POWERFEE_FILES),)
$(error 找不到源文件目录（$(CURDIR)/files 或 $(CURDIR)/../../files），请先运行 openwrt/build-apk.sh 同步源码)
endif

include $(INCLUDE_DIR)/package.mk

define Package/powerfee
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Dormitory electricity balance monitor with e-mail alerts
  URL:=https://github.com/aodianjun/PowerFeeWRT
  DEPENDS:=+jsonfilter +curl +ca-bundle
  PKGARCH:=all
endef

define Package/powerfee/description
  宿舍电量监控（OpenWrt 版）：定时查询宿舍电费余额，余额低于阈值时发邮件
  提醒，支持预警区提醒、充值恢复提醒和监控失效告警。

  除邮件外还可推送到 HTTP webhook（微信 / QQ 机器人等），并提供只读 HTTP
  查询端点 /cgi-bin/powerfee（uhttpd，8443）供机器人/局域网设备读取余额。

  纯脚本实现（busybox ash，不依赖架构相关二进制）；邮件投递优先用
  python3（smtplib），没有 python3 时用 msmtp（需自行 apk add msmtp）。

  微信推送：powerfee push status|set-serverchan|set-wecom|set-webhook|set-clawbot|off|test
  （推送通道管理器 /usr/lib/powerfee/push.py，需要 python3）—— 支持 Server酱、
  企业微信群机器人、ClawBot（WeClawBot-API，原生包或 Docker 容器）、自定义 webhook；
  powerfee wechat status|qr|enable|test（ClawBot 五步向导 /usr/lib/powerfee/wechat.py）。
  LuCI 的「服务 → 宿舍电量哨兵 → 微信推送」页就是这两条命令的图形界面。

  安装后不会自动启动服务，启用方法：
    1) powerfee rooms <关键词>       # 搜索房间号
       powerfee set-room <房间号>    # 设置要监控的房间
    2) uci set powerfee.mail.enabled=1
       uci set powerfee.mail.user='发件邮箱'
       uci set powerfee.mail.password='邮箱授权码'   # 不是登录密码
       uci set powerfee.mail.to='收件邮箱'
       uci commit powerfee
    3) powerfee test-mail            # 发一封测试邮件
    4) /etc/init.d/powerfee enable && /etc/init.d/powerfee start

  配置 /etc/config/powerfee（含邮箱授权码，权限 600）；
  运行状态与历史 /etc/powerfee/（已加入 sysupgrade 保留列表）。
  自检：powerfee selftest
endef

define Build/Prepare
	rm -rf $(PKG_BUILD_DIR)
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) $(POWERFEE_FILES)/. $(PKG_BUILD_DIR)/
endef

define Build/Compile
endef

# 助手模块整个目录都装：这里以前写死了文件名清单，结果 1.2.0 新增的
# chart.py / usage.py 漏打包（装上去曲线功能直接坏），所以改成遍历目录。
# 以后往 files/usr/lib/powerfee/ 加 *.py 不用再改这里。
#
# ⚠️ 循环变量必须写成 "$$$$f"（四个 $），不能写 "$$f"：
# 本 SDK（25.12 的 include/package-pack.mk）会把 install 段**展开两次**，
# 写 "$$f" 时第二遍展开后变成空串，构建会挂在 `install: cannot stat ''`。
# SDK 自带的 feeds/base/toolchain/Makefile 也是 "$$$$file" 这个写法。
define Package/powerfee/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/usr/bin/powerfee $(1)/usr/bin/powerfee
	$(INSTALL_DIR) $(1)/usr/lib/powerfee
	for f in $(PKG_BUILD_DIR)/usr/lib/powerfee/*.py; do \
		$(INSTALL_BIN) "$$$$f" $(1)/usr/lib/powerfee/; \
	done
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/etc/init.d/powerfee $(1)/etc/init.d/powerfee
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) $(PKG_BUILD_DIR)/etc/config/powerfee $(1)/etc/config/powerfee
	# 查询端点（uhttpd CGI，https://<IP>:8443/cgi-bin/powerfee?...）
	$(INSTALL_DIR) $(1)/www/cgi-bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/www/cgi-bin/powerfee $(1)/www/cgi-bin/powerfee
endef

# 配置文件（升级时按 conffile 处理：写进 /lib/apk/packages/powerfee.conffiles）
define Package/powerfee/conffiles
/etc/config/powerfee
endef

# 安装后置脚本。构建系统生成的 post-install 结构是
#   ... default_postinst <本段代码>
# 即本段代码在 default_postinst 之后执行（已用 apk adbdump 核对过产物）。
# 注意 25.12 的 default_postinst 对包里 /etc/init.d/* 的每个脚本都会
# 先 enable 再 start，而本包要求「装完不自动启用/启动服务」，所以首次安装时
# 显式 disable + stop 一次（升级时不动用户当前的启用/运行状态）。
# （IPKG_INSTROOT 非空 = 正在构建固件镜像，此时什么都不做，交给默认流程。）
define Package/powerfee/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	mkdir -p /etc/powerfee
	chmod 700 /etc/powerfee
	[ -f /etc/config/powerfee ] && chmod 600 /etc/config/powerfee
	grep -q '^/etc/powerfee$$' /etc/sysupgrade.conf 2>/dev/null || \
		echo '/etc/powerfee' >> /etc/sysupgrade.conf
	[ "$${PKG_UPGRADE}" = "1" ] || {
		/etc/init.d/powerfee disable >/dev/null 2>&1
		/etc/init.d/powerfee stop >/dev/null 2>&1
	}
}
endef

# 卸载后置脚本：清掉包自己的目录（python3 跑过 mail.py 会留下 __pycache__）。
# /etc/powerfee（状态与历史）和 /etc/config/powerfee（配置）是用户数据，保留。
define Package/powerfee/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	rm -rf /usr/lib/powerfee
	rm -f /www/cgi-bin/powerfee
}
endef

$(eval $(call BuildPackage,powerfee))
