#!/bin/sh
# 宿舍电量哨兵 · OpenWrt 版 安装脚本（在路由器上运行）
#
#   scp -r PowerFeeWRT root@192.168.1.1:/tmp/ && ssh root@192.168.1.1 sh /tmp/PowerFeeWRT/install.sh
#
# 或者把仓库拷到路由器上后：sh install.sh
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"

echo "==> 安装到 /usr/bin/powerfee、/usr/lib/powerfee、/etc/init.d/powerfee、/www/cgi-bin/powerfee"
mkdir -p /usr/lib/powerfee /www/cgi-bin

# 先写同目录下的临时文件再 mv：直接 cp 覆盖正在运行的脚本会因 ETXTBSY
# （Text file busy）失败。
# 用函数而不是逐条 cp/mv：这里以前有两条命令粘成一行（漏了换行），
# 结果主程序和 mail.py 都装不上，还因为 set -e 直接中断整个安装。
install_file() {
	# install_file <源文件> <目标路径>
	_tmp="$(dirname "$2")/.$(basename "$2").new"
	cp -f "$1" "$_tmp"
	chmod 755 "$_tmp"
	mv -f "$_tmp" "$2"
	echo "    $2"
}

install_file "$SRC/files/usr/bin/powerfee" /usr/bin/powerfee
# 助手模块：整个目录都装上（以后新增 *.py 不用改这里）
for _f in "$SRC"/files/usr/lib/powerfee/*.py; do
	install_file "$_f" "/usr/lib/powerfee/$(basename "$_f")"
done
install_file "$SRC/files/etc/init.d/powerfee" /etc/init.d/powerfee
# 查询端点（uhttpd CGI，默认关闭：notify.http_enabled=0）
install_file "$SRC/files/www/cgi-bin/powerfee" /www/cgi-bin/powerfee

mkdir -p /etc/powerfee
chmod 700 /etc/powerfee

# 配置文件：已存在就不覆盖（保留用户填好的邮箱授权码）
if [ -f /etc/config/powerfee ]; then
	echo "==> /etc/config/powerfee 已存在，保留现有配置（新版默认值可对照 files/etc/config/powerfee）"
else
	cp -f "$SRC/files/etc/config/powerfee" /etc/config/powerfee
	echo "==> 写入默认配置 /etc/config/powerfee"
fi
# 里面可能有邮箱授权码
chmod 600 /etc/config/powerfee

# 让配置在 sysupgrade 升级固件后保留
if ! grep -q '^/etc/powerfee$' /etc/sysupgrade.conf 2>/dev/null; then
	echo '/etc/powerfee' >>/etc/sysupgrade.conf
	echo "==> 已把 /etc/powerfee 加入 /etc/sysupgrade.conf（固件升级后保留）"
fi

/etc/init.d/powerfee enable
echo "==> 已设置开机自启"

if /etc/init.d/powerfee running >/dev/null 2>&1; then
	/etc/init.d/powerfee restart
	echo "==> 服务已重启"
else
	/etc/init.d/powerfee start
	echo "==> 服务已启动"
fi

echo ""
echo "安装完成。接下来："
echo "  1) powerfee set-room A101           # 设置要监控的房间（先 powerfee rooms A1 看列表）"
echo "  2) uci set powerfee.mail.enabled=1"
echo "     uci set powerfee.mail.user='你的邮箱'"
echo "     uci set powerfee.mail.password='邮箱授权码'"
echo "     uci set powerfee.mail.to='收件邮箱'"
echo "     uci commit powerfee"
echo "  3) powerfee test-mail                # 发一封测试邮件"
echo "  4) powerfee status                   # 看当前状态"
echo "  （可选）想推到微信/QQ 或开 HTTP 查询端点：见 README「推到微信 / QQ」"
