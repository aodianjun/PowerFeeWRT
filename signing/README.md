# signing/ —— 自签名密钥与产物

这个目录放**公钥**和**已签名的安装包 / 仓库索引**，供用户直接使用。
签名机制与完整流程见 [`../docs/signing.md`](../docs/signing.md)。

```
powerfee-signing.pem          公钥（P-256）。装到目标设备 /etc/apk/keys/ 后，
                              apk 就能校验本项目的签名，不需要 --allow-untrusted
repo/powerfee-1.0.1-r1.apk            已签名主包
repo/luci-app-powerfee-1.0.1-r1.apk   已签名界面包
repo/packages.adb                     已签名的自建仓库索引（含上面两个包）
```

用户侧最快路径：

```sh
cp powerfee-signing.pem /etc/apk/keys/
apk add repo/powerfee-1.0.1-r1.apk          # 不需要 --allow-untrusted
```

把 `repo/` 整个目录发布到 HTTP，加一行源就能 `apk add powerfee`：

```sh
echo 'http://<你的服务器>/powerfee/packages.adb' > /etc/apk/repositories.d/powerfee.list
apk update && apk add powerfee
```

作者侧（重新签名 / 发新版）：

```sh
sh openwrt/build-sign.sh --key ~/.powerfee-signing/powerfee-signing.key
```

⚠️ **私钥不在这个目录里，也永远不要提交进仓库**。私钥在构建机上
（本项目在 `/root/pf_signing/powerfee-signing.key`，仓库外），请自行备份；
丢失私钥就只能换新密钥并让所有用户重装公钥。
