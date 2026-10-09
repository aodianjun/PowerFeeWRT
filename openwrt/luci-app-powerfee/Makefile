#
# 宿舍电量哨兵 · OpenWrt 版 —— LuCI 界面包
#
# 依赖主程序包 powerfee。纯 JS/JSON 资源，与架构无关，PKGARCH:=all。
#
# 源文件在仓库的 luci/ 目录：
#   luci/htdocs/luci-static/resources/view/powerfee/*.js   -> /www/luci-static/resources/view/powerfee/
#   luci/root/usr/share/luci/menu.d/luci-app-powerfee.json -> /usr/share/luci/menu.d/
#   luci/root/usr/share/rpcd/acl.d/luci-app-powerfee.json  -> /usr/share/rpcd/acl.d/
# （luci/ 下 htdocs/ 与 root/ 两棵子树会整体拷到 /www 和 / 下，与官方 luci 包一致。）
#
# 构建：cp -R <仓库>/openwrt/luci-app-powerfee <SDK>/package/ && make package/luci-app-powerfee/compile V=s
# 或直接跑 <仓库>/openwrt/build-apk.sh <SDK 目录>
#
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-powerfee
PKG_VERSION:=1.2.4
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_MAINTAINER:=aodianjun

PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

# 源目录：优先用包目录自带的 luci/，否则回退到仓库根的 luci/
LUCI_SRC:=$(firstword $(wildcard $(CURDIR)/luci) $(wildcard $(CURDIR)/../../luci))
ifeq ($(LUCI_SRC),)
$(error 找不到 LuCI 源目录（$(CURDIR)/luci 或 $(CURDIR)/../../luci），请先运行 openwrt/build-apk.sh 同步源码)
endif

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-powerfee
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=LuCI app for powerfee (dormitory electricity monitor)
  URL:=https://github.com/aodianjun/PowerFeeWRT
  DEPENDS:=+powerfee +luci-base
  PKGARCH:=all
endef

define Package/luci-app-powerfee/description
  powerfee（宿舍电量监控）的 LuCI 网页界面：查看当前余额、状态与日均用量，
  配置要监控的房间、告警阈值和邮件提醒参数，并可开关后台监控服务。

  「微信推送」页顶部是推送通道选择器：Server酱（填 SendKey）/ 企业微信群机器人
  （粘 Webhook 地址）/ ClawBot（装服务 → 页面里直接显示登录二维码 → 在微信里发
  一条消息激活 → 一键写入推送配置并发测试消息）/ 自定义 webhook / 关闭。
  每个通道点一次「启用并测试」就写完配置并发一条测试消息，结果（含服务端原文）
  直接显示在页面上。
endef

define Build/Prepare
	rm -rf $(PKG_BUILD_DIR)
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) $(LUCI_SRC)/. $(PKG_BUILD_DIR)/
endef

define Build/Compile
endef

define Package/luci-app-powerfee/install
	$(INSTALL_DIR) $(1)/www
	$(CP) $(PKG_BUILD_DIR)/htdocs/. $(1)/www/
	$(INSTALL_DIR) $(1)
	$(CP) $(PKG_BUILD_DIR)/root/. $(1)/
endef

$(eval $(call BuildPackage,luci-app-powerfee))
