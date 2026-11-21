# 飞牛 fnOS 的 PBS 客户端（CLI）

这是一个第三方飞牛 fnOS 应用打包项目，仅包含命令行客户端（CLI）。安装时不会访问网络、添加 APT 源、执行 Debian 软件包维护脚本、注册全局命令或启动后台服务。安装后的预期可执行文件路径为
`/var/apps/fnos-pbs-client-cli/target/bin/proxmox-backup-client`。
图形界面（GUI）后续会作为独立软件包开发。本项目并非 Proxmox 或飞牛 fnOS 的官方发布。

## 许可证

本仓库自行编写的打包脚本、测试、工作流和文档采用 [Apache License 2.0](LICENSE)。本仓库不包含 Proxmox Backup Client 的源码或二进制。

FPK 中另行打包的 Proxmox Backup Client 是独立的上游程序，保持 [AGPL-3.0-or-later](https://www.gnu.org/licenses/agpl-3.0.html) 及其所含第三方组件各自的许可证；本仓库的 Apache-2.0 不适用于它们。打包会保留上游 Debian 软件包的版权和许可证声明。公开分发 FPK 时，仍须履行相应的许可证义务，包括提供与该二进制对应的源码和必要的构建材料。

## 当前进展

应用版本 0.1.0 的 x86-64 本地调试 FPK 已构建。构建前验证了 Proxmox Backup Client 静态 Debian 软件包 4.0.11-1，再用官方 fnpack v1.3.3 打包。固定的版本、来源和校验值见
`upstream/pbs-client-trixie-amd64.lock.json`。
从 FPK 中读回的客户端与上游文件逐字节一致；在隔离了主目录的 Linux 环境中，以非 root 用户成功运行了 `version`。
[首次 GitHub Actions 构建](https://github.com/Timandes/fnos-pbs-client-cli/actions/runs/44875035310)已通过，但尚未在 fnOS 实机验证安装、备份和恢复，也未验证正式 Release 的签名和上传流程。请勿发布或分发未签名的调试包。

## 使用已验证的离线材料构建

构建环境需要 Linux x86-64、Python 3.11 或更高版本、GnuPG、`dpkg-deb`、`tar` 和[官方 fnpack](https://developer.fnnas.com/docs/cli/fnpack/) v1.3.3。构建无需 root 权限，也不会修改主机的 APT 配置。

将从 [Proxmox 官方客户端仓库](https://pbs.proxmox.com/docs/installation.html)获取的四个原始文件放入 `inputs/`：

1. `proxmox-archive-keyring-trixie.gpg`，从[上游密钥环下载页](https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg)获取。
2. [Trixie InRelease](https://download.proxmox.com/debian/pbs-client/dists/trixie/InRelease)，文件名保持为 `InRelease`。
3. `dists/trixie/main/binary-amd64/` 下原始的 `Packages`、`Packages.gz` 或 `Packages.xz`，文件名和校验值须与该 InRelease 的记录一致。不要重新压缩或重命名索引。
4. 所选版本的 `proxmox-backup-client-static_*_amd64.deb`。在通过签名验证的 Packages 索引中找到对应条目的 `Filename`，该路径相对于 `https://download.proxmox.com/debian/pbs-client/`。

从通过验证的索引中选定准确的版本和 SHA-256，并明确固定这两个值。不能仅凭未验证的 `.deb` 文件算出的哈希来证明其来源。脚本先按上游文档核对密钥环的校验值，再验证 InRelease 的 OpenPGP 签名，核对索引的路径、SHA-256 和文件大小，最后核对固定版本软件包的哈希、大小和 Debian 软件包标识，之后才解包。非 x86-64 或使用动态链接的客户端会被拒绝；脚本也会检查上游版权声明。准备打包目录时不会执行未经信任的二进制文件。

在项目根目录运行以下命令，将示例中的文件名、版本、SHA-256 和工具路径替换为实际值。版本和校验值以锁定文件为准。

```sh
mkdir -p dist
python3 scripts/build_cli.py \
  --deb inputs/proxmox-backup-client-static_VERSION_amd64.deb \
  --inrelease inputs/InRelease \
  --index inputs/Packages.gz \
  --keyring inputs/proxmox-archive-keyring-trixie.gpg \
  --version VERSION --expected-sha256 SHA256 \
  --destination dist/cli-stage
(
  cd dist/cli-stage
  /absolute/path/to/official/fnpack build --nosign
  # 即使 fnpack 返回成功状态，也要确认生成了预期的产物。
  test -s fnos-pbs-client-cli.fpk || exit 1
  tar -tzf fnos-pbs-client-cli.fpk
  tar -xOzf fnos-pbs-client-cli.fpk app.tgz | tar -tzf -
  sha256sum fnos-pbs-client-cli.fpk > fnos-pbs-client-cli.fpk.sha256
)
```

`--nosign` 仅供本地调试，请勿发布未签名的调试包。不传此参数时，fnpack v1.3.3 需要有效的开发者证书。签名和发布是独立于本地调试构建的流程。排查问题时不要上传任何签名私钥。

通过以下命令计算包内客户端及打包目录中二进制文件的 SHA-256，并与 `app/third-party/upstream.json` 的记录核对：

```sh
cd dist/cli-stage
tar -xOzf fnos-pbs-client-cli.fpk app.tgz | tar -xOzf - bin/proxmox-backup-client | sha256sum
sha256sum app/bin/proxmox-backup-client
```

分发任何二进制包前，应保留上游和依赖的许可证及版权声明，核实并随包提供与该版本完全对应的 Proxmox 及所包含依赖的源码，以及所需的构建材料。打包脚本会保留上游 Debian 软件包的声明并记录二进制来源，但不会确认源码分发合规性，也不会分发软件包。

## 安装后调用客户端

安装包不会修改系统的 `PATH`。安装后可直接通过绝对路径调用客户端：

```sh
/var/apps/fnos-pbs-client-cli/target/bin/proxmox-backup-client version
```

如希望在当前终端会话中直接输入命令名，可自行将目录追加到 `PATH`：

```sh
export PATH="$PATH:/var/apps/fnos-pbs-client-cli/target/bin"
proxmox-backup-client version
```

上面的 `export` 只影响当前会话。如希望以后打开终端也生效，可以自行将这一行加入当前用户的 shell 启动配置，不需要修改系统级配置或软件包。

## 验证

```sh
FNPACK_TEST_BIN=/absolute/path/to/official/fnpack python3 -m unittest discover -s tests -v
```

不设置 `FNPACK_TEST_BIN` 时，仅跳过调用真实 fnpack 的测试。测试会在绕开真实签名输入的情况下使用专门构造的索引和软件包样本，并另外检查无效签名和错误密钥环是否被拒绝。这些样本测试不验证任何特定的上游发行版本。测试和打包准备过程都不会执行软件包维护脚本。测试使用的模拟二进制只有合成的 ELF 头，不能作为客户端使用。

请在获授权的测试 fnOS 设备上离线手动安装实际构建的包，以应用自己的运行账户执行
`/var/apps/fnos-pbs-client-cli/target/bin/proxmox-backup-client version`，
分别验证 DNS 解析和 TLS 校验，并将获准使用的测试文件备份到隔离的命名空间。恢复到空目录后比较校验值，同时检查卸载和升级行为。不要为了让测试通过而授予 root 权限、放宽文件权限或降低 TLS 校验要求。

## GitHub Actions 和 Release 发布

向 `main` 推送或提交拉取请求时，CLI FPK 工作流会运行构建检查，不访问发布密钥，也不会上传未签名的调试包。它下载官方 fnpack v1.3.3，按固定的 SHA-256 验证后才执行；随后验证 Proxmox 仓库的签名及固定版本 4.0.11-1 的 Debian 软件包。构建完成后，工作流重新读取 FPK，核对包内二进制和版权声明。上游下载失败、仓库元数据与固定的软件包不一致，或任一步验证失败，任务都会停止，不会改用其他来源或跳过检查。

发布 GitHub Release 会触发单独的上传任务。公开发布二进制包之前：

1. 独立审查该软件包准确对应的上游源码、已链接依赖的源码、打包和构建材料，以及许可证和版权声明。准备与软件包匹配的公开源码归档，并提供清楚的构建复现指南。只链接上游仓库并不够。
2. 审查后，将 `upstream/release-source.lock.json` 加到发行标签对应的源码中。字段包括 `asset`（不含目录的文件名，须以 `.tar.gz`、`.tar.xz` 或 `.tar.zst` 结尾）、`sha256`（源码归档的哈希）、`package_sha256`（与 Debian 软件包锁定文件中的哈希一致）、`reviewed_for`（准确的 Debian 软件包版本）、`reviewed_by`（审查记录或负责的维护者），以及 `coverage` 数组：
   `upstream-source`、`linked-dependency-source`、
   `packaging-and-build-materials`、`license-and-copyright-notices`。
   工作流会验证审查元数据和归档哈希，但不能自动判断源码是否完整。项目不提供未经审查的源码锁定文件。
3. 配置 GitHub 的 `fpk-release` 环境，仅允许受保护的发行标签使用它，并在支持的情况下要求发布审批。仅使用飞牛官方签发的开发者证书及其匹配的私钥，将两个完整文件分别做 Base64 编码，存入环境机密 `FNPACK_KEY_B64` 和 `FNPACK_CERT_B64`。Base64 不是加密，不要将这些内容粘贴到日志、Issue 或聊天中，也不要提交到仓库。官方 fnpack v1.3.3 的 `build --help` 列出了 `build --key ... --cert ...`；不接受自签名测试证书。
4. 标签使用 `v` 加上与 fnOS manifest 一致的版本号。先把审查通过的源码归档附到 Release 草稿，再正式发布。签名前，工作流会验证同一 Release 中已附的源码资产及其校验值；只有输出中包含 fnpack 的原生签名记录时，才上传签名 FPK 和对应的 SHA-256 校验文件，不覆盖已有资产。

未签名构建和发布前的拦截检查已在本地测试。使用官方证书完成签名、上传，并在实际 fnOS 设备上安装的完整发布流程尚未验证。正式公开发布前，请在受控环境中确认签名包结构和安装结果。如果签名工具或归档布局发生变化，未通过检查的产物不会上传。
