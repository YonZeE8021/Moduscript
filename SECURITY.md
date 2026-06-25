# 安全策略

## 受支持版本

| 版本 | 支持 |
|------|------|
| `main`（最新） | 是 |

## 报告漏洞

请**不要**在公开的 GitHub Issue 中报告安全问题。

请使用 **GitHub Security Advisories**（仓库 Security 页的 “Report a vulnerability”），或通过官方项目页联系维护者。

## 敏感配置

自托管部署须保护以下密钥：

| 密钥 | 位置 |
|------|------|
| JWT 签名密钥 | `.env` → `JWT_SECRET` |
| 管理后台密码 | `.env` → `MCMOD_ADMIN_PASSWORD` |
| Deploy 预共享密钥 | `deploy/keys/psk.hex` |
| LLM API 密钥 | `.env` 或管理后台 → `data/admin/` |

切勿提交到 Git。若已泄露，请立即轮换并重启服务。

## 已知部署风险

- 用户数据以 **JSON 明文** 存放在 `data/`（见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)）。
- 默认 `.env` 值不安全；对外暴露网络前请设置强密钥。
- 生产环境请启用 `MCMOD_REQUIRE_STRONG_SECRETS=true`。
- 限制对 `/admin.html` 与 deploy 接收端口的网络访问。

## AGPL-3.0

在网络上部署修改版时，须按 AGPL-3.0 向用户提供对应源代码。见 [docs/LEGAL_NOTES.md](docs/LEGAL_NOTES.md)。
