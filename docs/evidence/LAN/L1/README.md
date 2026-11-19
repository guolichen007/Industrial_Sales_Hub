# L1 安装包与部署证据

## 上游组件来源与校验（构建机下载，客户端与目标主机无需联网）

| 组件 | 版本 | SHA256 | 来源核验 |
|---|---|---|---|
| Python 嵌入式包 `python-3.12.10-embed-amd64.zip` | 3.12.10 | `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3` | python.org sigstore 包：`sigstore verify identity --cert-identity thomas@python.org --cert-oidc-issuer https://accounts.google.com` → **OK** |
| PostgreSQL Windows x64 二进制 `postgresql-16.16-1-windows-x64-binaries.zip` | 16.16-1（16.x 最新） | `5c0d06f3d26a929ecb36dd931409f1e51a4181355794a259b90aae8bffc57a9f` | `postgres.exe`、`pg_ctl.exe` Authenticode 校验 **OK**，签名者 `EnterpriseDB Corporation`（DigiCert G4 代码签名 CA） |
| Microsoft VC++ 2015-2022 x64 | 微软滚动版 | `cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b`（本次构建） | Authenticode **OK**，签名者 Microsoft Corporation |
| WinSW `WinSW-x64.exe` | 2.12.0 | `05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da` | 上游未签名（NOT_SIGNED），记录哈希 |
| Python 依赖 | 见 `licenses/SBOM.json` | 包内 `SHA256SUMS.txt` | 全部 `cp312-win_amd64` 或纯 Python wheel；包内无 Linux `.so` |
| 字体 | DroidSansFallbackFull 1.00c、DejaVu Sans 2.37 | `backend/fonts/SHA256SUMS` | Apache-2.0、Bitstream Vera 许可，许可证随包 |

PostgreSQL 包裁剪：去掉 pgAdmin 4（约 1.8 万个文件，816 MB）、StackBuilder、文档、头文件、`.lib/.pdb`，保留 1604 个文件（约 94 MB）。

## 包内容核验（解压后）

- 6458 个文件，`sha256sum -c SHA256SUMS.txt` 全部一致；解压后约 215 MB，ZIP 约 116 MB（首轮构建，后续以最终提交重建）。
- 不含：测试目录、`*.sqlite3`、`.env`/`app.env`、证书/私钥、PostgreSQL 超级用户口令文件、`__pycache__`；不含测试与 e2e 使用的口令。
- `seed_demo.py` 随源码存在，但 LAN 模式下执行会被拒绝（已实测）。
- `runtime/python/python312._pth` 包含 `Lib\site-packages` 与 `..\..\app\backend`。
- 用包内 `app/` 与包内纯 Python 依赖在 Linux 上以 LAN 模式完成：迁移 → 导入主体配置 → 初始化管理员 → Waitress 启动 → 局域网 IP 健康检查 200 → 前端首页 200 → 登录 200。

## LAN 模式运行核验（Linux 沙箱，Waitress + PostgreSQL 16）

| 检查 | 结果 |
|---|---|
| `seed_demo` 在 LAN 模式 | 拒绝 |
| 健康检查（回环 / 局域网 IP） | 200 / 200 |
| 非法 Host 头 | 400 |
| 伪造 `X-Forwarded-Proto: https` | Cookie 仍非 Secure（代理头未被信任） |
| 演示账号 `leader/Demo@2026` 登录 | 400（不存在） |
| 数据库监听 | 仅 `127.0.0.1` |
| 未知 `/api/` 路径 | JSON 404（非调试页） |
| 配置错误（缺密钥/开发密钥/DEBUG/无 PG/无 ALLOWED_HOSTS/`*`/错误模式/配置文件不存在） | 全部在启动时拒绝并给出中文原因 |

## Windows 脚本（静态）

PowerShell 7.4.6 解析器 0 错误；PSScriptAnalyzer 1.22.0 通用规则 0 告警、Windows PowerShell 5.1（Server 2019 profile）兼容规则 0 告警。
两轮复审修复：服务账户设置方式、ASCII 路径、根目录与服务目录 ACL、`-X utf8`、重复安装占用、升级复制与 ACL、tzdata、UTF-8 文件名 ZIP。
**未在 Windows 上实际执行。**
