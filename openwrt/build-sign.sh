#!/bin/sh
# ============================================================================
# 宿舍电量哨兵 · OpenWrt 版 —— 自签名脚本（apk-tools 3 / OpenWrt 25.x）
#
# 用法：
#   sh openwrt/build-sign.sh [选项]
#
#   --key <私钥>     签名私钥（PEM，EC P-256 或 RSA）。默认 $POWERFEE_SIGNING_KEY，
#                    再默认 $HOME/.powerfee-signing/powerfee-signing.key。
#   --init-key       没有私钥时先生成一对（需要 openssl；生成到 --key 指定的位置，
#                    公钥同步到 signing/powerfee-signing.pem）。私钥绝不要提交进仓库！
#   --apk <apk>      指定带 mkpkg/adbsign/mkndx 的 apk-tools 3 可执行文件。
#                    默认依次尝试：$POWERFEE_APK、$OPENWRT_SDK/staging_dir/host/bin/apk、
#                    /root/sdk/*/staging_dir/host/bin/apk、PATH 里的 apk（并验证它支持
#                    adbsign/mkndx；OpenWrt 设备自带的精简版 apk 不支持，会自动跳过）。
#   --in <目录>      未签名 .apk 所在目录，默认 <仓库>/apk
#   --out <目录>     签名产物输出目录，默认 <仓库>/signing/repo
#   -h|--help        帮助
#
# 做的事（每一步都能单独复现，命令都写在下面注释里）：
#   1) 校验/生成密钥对：私钥留在构建机（脚本会拒绝把私钥写进仓库目录）
#   2) 逐个签名 apk/ 下的 .apk：
#        apk --allow-untrusted adbsign --sign-key <私钥> <包>
#      注意两个 apk-tools 3.0.5 的实测行为：
#        * 读取未签名输入需要 --allow-untrusted（数据块的信任检查在读取侧）；
#        * 一次调用只会给**第一个**文件写签名块（signatures_written 只置位一次），
#          所以脚本对每个包单独调一次。
#   3) 生成并签名自建仓库索引（文件名必须是 <name>-<version>.apk）：
#        apk --allow-untrusted mkndx --sign-key <私钥> -o packages.adb *.apk
#   4) 把公钥拷到 signing/powerfee-signing.pem（进仓库，给用户装到 /etc/apk/keys/）
#   5) 自检：用临时 keys 目录 verify 每个签名包和索引，打印 sha256
#
# 产物（默认 signing/repo/）：
#   powerfee-1.2.4-r1.apk            已签名，可直接 apk add（装过公钥后）
#   luci-app-powerfee-1.2.4-r1.apk   同上
#   powerfee-chat-1.1-r1.apk         微信查询桥接（纯 python3 脚本包）
#   packages.adb                     已签名的自建仓库索引（配合上面的包做源）
#
#   注意：签名对象是 --in 目录（默认 apk/）下**当时存在的全部 .apk**——新包
#   （如 powerfee-chat）只要构建产物落进 apk/ 就会被一起签名、并进 packages.adb；
#   只想签某个包时用 --in <只含该包的目录>，避免顺手重签其它包（ECDSA 每次签名
#   字节都不同，重签会改变已有签名文件的 sha256）。
#
# 用户侧（目标设备）：
#   wget -O /etc/apk/keys/powerfee-signing.pem <公钥 URL>
#   apk add /path/to/powerfee-1.2.4-r1.apk        # 不需要 --allow-untrusted
#   # 或者把自建源加进 /etc/apk/repositories.d/ 后 apk update && apk add powerfee
#
# 为什么官方签名拿不到：OpenWrt 官方包/索引是 buildbot 的私钥签的（公钥随固件
# 装在 /etc/apk/keys/），第三方无法让 OpenWrt 代签；只有包被官方 feed 收录、
# 进入官方构建流程，才会带上官方签名。自建包的正解就是本脚本。
# ============================================================================
set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

IN_DIR="$REPO/apk"
OUT_DIR="$REPO/signing/repo"
PUB_OUT="$REPO/signing/powerfee-signing.pem"
KEY="${POWERFEE_SIGNING_KEY:-$HOME/.powerfee-signing/powerfee-signing.key}"
INIT_KEY=0
APK="${POWERFEE_APK:-}"

usage() {
	sed -n '2,51p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
	case "$1" in
		--key)      [ -n "$2" ] || { echo "错误: --key 需要参数" >&2; exit 1; }; KEY="$2"; shift 2 ;;
		--key=*)    KEY="${1#--key=}"; shift ;;
		--init-key) INIT_KEY=1; shift ;;
		--apk)      [ -n "$2" ] || { echo "错误: --apk 需要参数" >&2; exit 1; }; APK="$2"; shift 2 ;;
		--apk=*)    APK="${1#--apk=}"; shift ;;
		--in)       [ -n "$2" ] || { echo "错误: --in 需要参数" >&2; exit 1; }; IN_DIR="$2"; shift 2 ;;
		--in=*)     IN_DIR="${1#--in=}"; shift ;;
		--out)      [ -n "$2" ] || { echo "错误: --out 需要参数" >&2; exit 1; }; OUT_DIR="$2"; shift 2 ;;
		--out=*)    OUT_DIR="${1#--out=}"; shift ;;
		-h|--help)  usage; exit 0 ;;
		*) echo "错误: 未知参数 '$1'" >&2; usage >&2; exit 1 ;;
	esac
done

# ---- 1. 找 apk-tools 3（必须带 adbsign/mkndx） ------------------------------

apk_supports_tools() {
	[ -n "$1" ] && [ -x "$1" ] || return 1
	"$1" adbsign --help 2>&1 | grep -q '^Usage: apk adbsign'
}

if [ -n "$APK" ]; then
	apk_supports_tools "$APK" || { echo "错误: $APK 不支持 adbsign/mkndx（精简版 apk？）" >&2; exit 1; }
else
	for cand in \
		"${OPENWRT_SDK:-}/staging_dir/host/bin/apk" \
		/root/sdk/*/staging_dir/host/bin/apk \
		"$(command -v apk 2>/dev/null || true)"
	do
		[ -n "$cand" ] || continue
		if apk_supports_tools "$cand"; then APK="$cand"; break; fi
	done
fi
[ -n "$APK" ] && apk_supports_tools "$APK" || {
	echo "错误: 找不到带 adbsign/mkndx 的 apk-tools 3。" >&2
	echo "  提示: 用 --apk 指定 SDK 的 staging_dir/host/bin/apk（OpenWrt 设备自带的 apk 是精简版，签不了）。" >&2
	exit 1
}
echo "==> apk 工具: $APK"

# ---- 2. 密钥 ----------------------------------------------------------------

need_openssl() {
	for c in "${OPENWRT_SDK:-}/staging_dir/host/bin/openssl" "$(dirname "$APK")/openssl" \
		"$(command -v openssl 2>/dev/null || true)"
	do
		[ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
	done
	return 1
}

if [ ! -f "$KEY" ]; then
	[ "$INIT_KEY" = 1 ] || {
		echo "错误: 找不到私钥 $KEY" >&2
		echo "  第一次使用请加 --init-key 生成；或者用 --key 指定已有的私钥。" >&2
		exit 1
	}
	OSSL=$(need_openssl) || { echo "错误: 需要 openssl 生成密钥（也可用 SDK 的 openssl）" >&2; exit 1; }
	mkdir -p "$(dirname "$KEY")"
	echo "==> 生成新密钥对（EC P-256）: $KEY"
	"$OSSL" ecparam -name prime256v1 -genkey -noout -out "$KEY" 2>/dev/null
	chmod 600 "$KEY"
fi

# 安全护栏：私钥不能位于仓库目录内（防止误提交 / 误推送）
case "$(CDPATH= cd -- "$(dirname -- "$KEY")" && pwd)" in
	"$REPO"|"$REPO"/*)
		echo "错误: 私钥 $KEY 在仓库目录内！请把它放到仓库外（例如 ~/.powerfee-signing/）。" >&2
		exit 1
		;;
esac
[ -f "$PUB_OUT" ] || { OSSL=$(need_openssl); "$OSSL" ec -in "$KEY" -pubout > "$PUB_OUT" 2>/dev/null; }

# 公钥每次从私钥重新导出（幂等）：换了私钥就不会留下过期的公钥
OSSL=$(need_openssl) || { echo "错误: 需要 openssl 导出公钥" >&2; exit 1; }
mkdir -p "$(dirname "$PUB_OUT")"
"$OSSL" ec -in "$KEY" -pubout > "$PUB_OUT" 2>/dev/null
echo "==> 公钥已导出: $PUB_OUT"

# ---- 3. 签名每个包 ----------------------------------------------------------

[ -d "$IN_DIR" ] || { echo "错误: 找不到输入目录 $IN_DIR" >&2; exit 1; }
mkdir -p "$OUT_DIR"

TMP_KEYS=$(mktemp -d "${TMPDIR:-/tmp}/pf-sign-keys.XXXXXX")
trap 'rm -rf "$TMP_KEYS"' EXIT INT TERM
cp "$PUB_OUT" "$TMP_KEYS/"

FOUND=0
for f in "$IN_DIR"/*.apk; do
	[ -f "$f" ] || continue
	FOUND=$((FOUND + 1))
	# 从包元数据里取规范文件名 <name>-<version>.apk（自建源必须这样命名）
	name=$("$APK" adbdump "$f" 2>/dev/null | sed -n 's/^  name: //p' | head -n1)
	ver=$("$APK" adbdump "$f" 2>/dev/null | sed -n 's/^  version: //p' | head -n1)
	if [ -z "$name" ] || [ -z "$ver" ]; then
		base=$(basename "$f" .apk)
		name=${base%%_*}
		ver=unknown
		echo "警告: $f 解析不出 name/version，退回文件名解析（$name-$ver）" >&2
	fi
	out="$OUT_DIR/$name-$ver.apk"
	echo "==> 签名 $name $ver"
	cp "$f" "$out"
	# 每个包单独调用一次（apk 3.0.5 一次调用只给第一个文件写签名）
	"$APK" --allow-untrusted adbsign --sign-key "$KEY" "$out"
	"$APK" --keys-dir "$TMP_KEYS" verify "$out"
done
[ "$FOUND" -gt 0 ] || { echo "错误: $IN_DIR 下没有 .apk" >&2; exit 1; }

# ---- 4. 生成并签名仓库索引 --------------------------------------------------

echo "==> 生成签名索引 packages.adb"
cd "$OUT_DIR"
rm -f packages.adb
"$APK" --allow-untrusted mkndx --sign-key "$KEY" -o packages.adb ./*.apk
"$APK" --keys-dir "$TMP_KEYS" verify packages.adb

# ---- 5. 汇总 ----------------------------------------------------------------

echo
echo "==> 完成。产物在 $OUT_DIR"
echo "    公钥: $PUB_OUT"
	echo "    用户侧安装："
	echo "      cp powerfee-signing.pem /etc/apk/keys/"
	echo "      apk add $OUT_DIR/powerfee-1.2.4-r1.apk       # 文件名以实际版本为准"
	echo "      apk add $OUT_DIR/powerfee-chat-1.1-r1.apk    # 微信查询桥接（可选）"
echo "    自建源：把 $OUT_DIR 发布到 HTTP，然后"
echo "      echo '<URL>/packages.adb' > /etc/apk/repositories.d/powerfee.list && apk update && apk add powerfee"
echo
if command -v sha256sum >/dev/null 2>&1; then
	sha256sum "$OUT_DIR"/*.apk "$OUT_DIR"/packages.adb "$PUB_OUT"
fi
