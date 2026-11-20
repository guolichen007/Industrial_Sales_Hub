# RC2 HTTPS 内网证书配置手册（本地 TLS）

> 结论先行：**HTTP 只用于隔离内网试用**。正式使用应通过内部 CA 签发证书 + 反向代理提供 HTTPS；
> 不得使用自签证书并要求员工点击“继续访问不安全网站”。甲方若在没有证书的情况下要求正式使用，必须有**书面授权**并单独记录为“安全例外”，
> 不能记为安全 PASS。本手册的配置**尚未在甲方 Windows 主机实测**（见 RC2 实机验收手册），记录为“可复现配置，待现场验证”。

## 1. 架构

```
浏览器 ──HTTPS 443──► 反向代理（IIS ARR / Caddy / nginx for Windows，持有内部 CA 证书）──HTTP──► 127.0.0.1:8000 Waitress（本系统）
                                                                                        └─► 127.0.0.1:55432 PostgreSQL（仅本机）
```

- 应用只监听 `127.0.0.1`，防火墙**不开放 8000**；只放行代理的 443（仅专用/域网络、本地子网）。
- 应用信任且只信任来自 127.0.0.1 的 `X-Forwarded-Proto/For/Host`（`ISH_TRUSTED_PROXY=127.0.0.1`、`ISH_TRUST_PROXY_HEADER=1`）。
- 会话与 CSRF Cookie 为 `Secure`；HSTS 1 天、不含子域名、不 preload（避免影响甲方其他内网站点）。

## 2. 新装即 HTTPS 模式

```powershell
.\01-安装部署.bat -Https -AllowedRemote LocalSubnet
```
效果（已由安装脚本实现）：`ISH_LISTEN=127.0.0.1:<端口>`、`ISH_HTTPS=1`、`ISH_TRUST_PROXY_HEADER=1`、`ISH_TRUSTED_PROXY=127.0.0.1`，
`DJANGO_CSRF_TRUSTED_ORIGINS` 为 `https://<主机名/IP>:<端口>`；**不创建**应用端口的防火墙规则。安装后按第 4 节配置代理与证书。

## 3. 已有 HTTP 安装改为 HTTPS

编辑 `config\app.env`（管理员），修改/增加：
```
ISH_HTTPS=1
ISH_LISTEN=127.0.0.1:8000
ISH_TRUST_PROXY_HEADER=1
ISH_TRUSTED_PROXY=127.0.0.1
DJANGO_ALLOWED_HOSTS=sales.corp.example,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://sales.corp.example
```
然后：删除旧规则 `Get-NetFirewallRule -DisplayName 'Industrial Sales Hub*' | Remove-NetFirewallRule`；重启应用服务 `Restart-Service IndustrialSalesHub-App`。

## 4. 证书与反向代理（二选一）

### 4.1 甲方已有内部 CA（AD CS）——推荐
1. 由 IT 为 `sales.corp.example`（及主机名/IP 作为 SAN）签发服务器证书，导入主机“本地计算机\个人”证书存储。
2. 客户端通过域组策略已信任内部根 CA（无需逐台操作）。

### 4.2 无 AD CS 的小型内网（仍需“自建内部 CA”，不是直接自签站点证书）
1. 管理员在**离线电脑**上建立根 CA（例如 `New-SelfSignedCertificate -Type Custom -KeyUsage CertSign,CRLSign -TextExtension '2.5.29.19={critical}{text}ca=1' ...`，或使用 XCA 等工具），根私钥离线保管。
2. 用根 CA 签发站点证书（SAN 包含内网域名/主机名/IP）。
3. 把**根证书**（不是站点证书）分发到每台客户端：`certutil -addstore -f Root 内网根CA.cer`（管理员），或通过组策略下发。
4. 在客户端浏览器确认地址栏无告警。**不要**让员工手动“继续访问”。

### 4.3 反向代理配置示例（IIS + URL Rewrite + ARR）
- 安装 IIS、URL Rewrite、Application Request Routing；ARR 中启用代理。
- 站点绑定 https:443 + 4.1/4.2 的证书；新增入站规则：`(.*)` → `http://127.0.0.1:8000/{R:1}`，并设置服务器变量 `HTTP_X_FORWARDED_PROTO=https`。
- 上传大小：`maxAllowedContentLength=31457280`（与应用 30 MB 上限一致）。
- 防火墙：`New-NetFirewallRule -DisplayName 'Industrial Sales Hub HTTPS (443)' -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -Profile Private,Domain -RemoteAddress LocalSubnet`。

（Caddy/nginx for Windows 同理：TLS 终止于代理，`proxy_set_header X-Forwarded-Proto https`，上游 `127.0.0.1:8000`。）

## 5. 验收（现场执行并截图）

| # | 检查 | 期望 |
|---|---|---|
| 1 | 客户端访问 `https://sales.corp.example/` | 无证书告警；证书链到内部根 CA |
| 2 | 客户端访问 `http://<主机IP>:8000/` | 连接失败（防火墙未放行、应用只监听回环） |
| 3 | F12 → Application → Cookies | `ish_session`、`csrftoken` 均为 Secure、HttpOnly（会话）、SameSite=Lax |
| 4 | 响应头 | `Strict-Transport-Security: max-age=86400`（无 includeSubDomains/preload） |
| 5 | 从另一来源页面 POST 登录（可用 `curl -H "Origin: https://evil.example"`） | 403（CSRF 来源校验；RC2 登录也强制 CSRF） |
| 6 | 客户端伪造 `X-Forwarded-For` | 应用日志记录的是代理转发的真实客户端地址（只信任 127.0.0.1 代理） |
| 7 | `06-检查系统.bat` | 不再出现“HTTP 隔离内网试用”警告 |

自动化证据（开发环境）：`backend/tests/test_rc2_tls_settings.py`（Secure Cookie、HSTS 参数、仅在声明代理时信任代理头、CSRF 只接受可信 https 来源、LAN 拒绝 DEBUG/`*`/短密钥）。

## 6. 无证书的正式例外

若甲方书面决定在隔离内网先以 HTTP 正式运行：保存书面授权（日期、范围、期限、责任人）到交付文档；`06-检查系统` 会持续提示；
不得将该状态记为安全门禁 PASS；在例外到期前完成第 4 节。
