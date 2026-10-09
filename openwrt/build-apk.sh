#!/bin/sh
# ============================================================================
# 一条命令重建 powerfee / luci-app-powerfee / powerfee-chat 的 .apk 安装包
#
# 用法：
#   sh openwrt/build-apk.sh /path/to/openwrt-sdk-25.12.2-x86-64_gcc-14.3.0_musl.Linux-x86_64
#
# 做的事：
#   1) 把仓库的 files/ 同步进 openwrt/powerfee/files/（包目录自包含，方便整体拷进 SDK）
#      把仓库的 integrations/wechat-clawbot-bridge/ 同步进 openwrt/powerfee-chat/files/
#      （同样的树形布局：usr/bin/powerfee-chat、etc/init.d/powerfee-chat、
#        etc/powerfee-chat.conf、usr/share/doc/powerfee-chat/README.md）
#   2) 把 openwrt/powerfee、openwrt/luci-app-powerfee、openwrt/powerfee-chat 拷进 SDK/package/
#   3) make package/<pkg>/compile（-j1，低配机器/容器上更稳）
#   4) 产物拷到 <仓库>/apk/powerfee_1.2.4_all.apk、apk/luci-app-powerfee_1.2.4_all.apk、
#      apk/powerfee-chat_1.1_all.apk
#
# 如果 luci/ 目录不存在（界面源码未就绪），只构建主包 + 桥接包。
# 注意：构建机必须是 glibc 的 x86_64 Linux（SDK 自带的宿主工具是 glibc 二进制，
# 不能直接在 musl 的 OpenWrt 上跑；官方 SDK 也不能在 Windows 上跑）。
# ============================================================================
set -e

SDK="$1"
[ -n "$SDK" ] || { echo "用法: $0 <OpenWrt SDK 目录>"; exit 1; }
[ -f "$SDK/rules.mk" ] || { echo "错误: $SDK 不像 OpenWrt SDK 目录（找不到 rules.mk）"; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> 1/4 同步源码到包目录"
rm -rf "$REPO/openwrt/powerfee/files"
cp -R "$REPO/files" "$REPO/openwrt/powerfee/files"

# 桥接包：integrations/wechat-clawbot-bridge/（扁平命名）-> 包内安装树布局。
# files/ 只是「整包拷进 SDK」用的同步副本，单一事实来源始终是 integrations/。
CHAT_SRC="$REPO/integrations/wechat-clawbot-bridge"
[ -f "$CHAT_SRC/powerfee-chat.py" ] || { echo "错误: 找不到 $CHAT_SRC/powerfee-chat.py"; exit 1; }
rm -rf "$REPO/openwrt/powerfee-chat/files"
mkdir -p "$REPO/openwrt/powerfee-chat/files/usr/bin" \
         "$REPO/openwrt/powerfee-chat/files/etc/init.d" \
         "$REPO/openwrt/powerfee-chat/files/usr/share/doc/powerfee-chat"
cp "$CHAT_SRC/powerfee-chat.py"           "$REPO/openwrt/powerfee-chat/files/usr/bin/powerfee-chat"
cp "$CHAT_SRC/powerfee-chat.init"         "$REPO/openwrt/powerfee-chat/files/etc/init.d/powerfee-chat"
cp "$CHAT_SRC/powerfee-chat.conf.example" "$REPO/openwrt/powerfee-chat/files/etc/powerfee-chat.conf"
cp "$CHAT_SRC/README.md"                  "$REPO/openwrt/powerfee-chat/files/usr/share/doc/powerfee-chat/README.md"

BUILD_LUCI=0
if [ -d "$REPO/luci" ] && [ -n "$(ls -A "$REPO/luci" 2>/dev/null)" ]; then
	BUILD_LUCI=1
	rm -rf "$REPO/openwrt/luci-app-powerfee/luci"
	cp -R "$REPO/luci" "$REPO/openwrt/luci-app-powerfee/luci"
	echo "    luci/ 已就绪，会一起构建界面包"
else
	echo "    luci/ 不存在或为空，只构建主包 powerfee + 桥接包 powerfee-chat"
fi

echo "==> 2/4 拷进 $SDK/package/"
rm -rf "$SDK/package/powerfee"
cp -R "$REPO/openwrt/powerfee" "$SDK/package/powerfee"
rm -rf "$SDK/package/powerfee-chat"
cp -R "$REPO/openwrt/powerfee-chat" "$SDK/package/powerfee-chat"
if [ "$BUILD_LUCI" = 1 ]; then
	rm -rf "$SDK/package/luci-app-powerfee"
	cp -R "$REPO/openwrt/luci-app-powerfee" "$SDK/package/luci-app-powerfee"
fi

echo "==> 3/4 编译"
cd "$SDK"
[ -f .config ] || make defconfig
make -j1 package/powerfee/compile V=s
make -j1 package/powerfee-chat/compile V=s
if [ "$BUILD_LUCI" = 1 ]; then
	make -j1 package/luci-app-powerfee/compile V=s
fi

echo "==> 4/4 收集产物到 $REPO/apk/"
mkdir -p "$REPO/apk"
# 注意用 powerfee-[0-9]* 精确匹配主包，别把 powerfee-chat-*.apk 也抓进来
P_APK="$(find "$SDK/bin" -name 'powerfee-[0-9]*.apk' | head -n1)"
[ -n "$P_APK" ] || { echo "错误: 没找到 powerfee 的 .apk 产物"; exit 1; }
cp "$P_APK" "$REPO/apk/powerfee_1.2.4_all.apk"
echo "    $P_APK"
echo "    -> apk/powerfee_1.2.4_all.apk"
C_APK="$(find "$SDK/bin" -name 'powerfee-chat-[0-9]*.apk' | head -n1)"
[ -n "$C_APK" ] || { echo "错误: 没找到 powerfee-chat 的 .apk 产物"; exit 1; }
cp "$C_APK" "$REPO/apk/powerfee-chat_1.1_all.apk"
echo "    $C_APK"
echo "    -> apk/powerfee-chat_1.1_all.apk"
if [ "$BUILD_LUCI" = 1 ]; then
	L_APK="$(find "$SDK/bin" -name 'luci-app-powerfee-[0-9]*.apk' | head -n1)"
	[ -n "$L_APK" ] || { echo "错误: 没找到 luci-app-powerfee 的 .apk 产物"; exit 1; }
	cp "$L_APK" "$REPO/apk/luci-app-powerfee_1.2.4_all.apk"
	echo "    $L_APK"
	echo "    -> apk/luci-app-powerfee_1.2.4_all.apk"
fi

echo "==> 完成"
sha256sum "$REPO/apk/"*.apk
