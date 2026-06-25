# 部署与运行指南

面向**单机/生产运行**与**开发环境**。TCP 增量推送见 [deploy/README.md](../deploy/README.md)。

---

## 环境要求

- Python 3.11+
- Java JDK 17+（Gradle 编译模组）
- Git（会话分支 / 工作区 checkpoint）
- [Claude Code CLI](https://code.claude.com/docs/en/agent-sdk/python)（真实 Agent；可用 `USE_MOCK_SESSIONS=true` 跳过）
- Playwright Chromium（Fabric 模板 bootstrap）
- Windows 为主开发环境；Linux 见 [LINUX_MIGRATION.md](LINUX_MIGRATION.md)

---

## 快速启动

### Windows（推荐）

```powershell
.\scripts\setup.ps1 -AdminEmail your@email.com
.\scripts\run.ps1
```

也可双击 `server/start.bat`。

### 手动安装

```bash
cd server
pip install -r requirements.txt
playwright install chromium
cp ../.env.example ../.env
python main.py
```

访问 http://127.0.0.1:8000/

---

## 首次管理员

1. 在 `.env` 设置 `MCMOD_BOOTSTRAP_ADMIN_EMAIL=your@email.com`
2. 使用该邮箱注册，自动获得 `admin` 角色
3. 登录后访问 `/admin.html` 配置统一 LLM API

---

## 开发 / 生产 / 部署

| 场景 | 做什么 |
|------|--------|
| **本地开发** | `scripts/run.ps1`，只起 Web |
| **生产 integrated** | `Moduscript.bat`：Web + deploy receiver |
| **代码推到生产** | 开发机 `deploy/cli.py deploy`（见 [deploy/README.md](../deploy/README.md)） |

---

## 数据目录

默认 `./data/`（`MCMOD_DATA_DIR` 可改），**不进 Git**。含用户、会话 JSON、工作区与日志。

---

## 开发模式

`USE_MOCK_SESSIONS=true`：内存 mock 会话，无需 Claude CLI。

---

## 生产建议

- 强随机 `JWT_SECRET`；可选 `MCMOD_REQUIRE_STRONG_SECRETS=true`
- HTTPS 反代（nginx / Caddy）
- 定期备份 `data/`
- 限制 `/admin.html` 与 deploy 端口访问
- 密钥泄露后轮换 `.env` 与 `deploy/keys/psk.hex` 并重启服务

详见 [AGENT_SDK.md](AGENT_SDK.md)、[SESSION_BRANCH.md](SESSION_BRANCH.md)。
