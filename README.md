# Moduscript 模词成稿

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)

Minecraft 模组/插件 Agent 编写平台。纯 HTML / CSS / JavaScript 前端 + FastAPI 后端，集成 [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python)。自托管 Web 平台，用 Claude Agent 根据自然语言规格编写 Minecraft Fabric 模组，支持规划模式、会话分支与 Modrinth 参考集成。采用 **AGPL-3.0** 许可。

## 主要功能

- **任务配置**：MC 版本、加载器、平台、构想描述、其他要求、Modrinth 参考模组
- **编写工作台**（`session.html`）：SSE 实时进度、Agent 思考/工具卡片、用户确认（AskUserQuestion）；上下文压缩后自动 resume 续写直至 jar 或轮次上限
- **规划模式**（`plan.html`）：多轮需求细化、方案树、定稿 handoff 到编写（见 [docs/PLAN_MODE.md](docs/PLAN_MODE.md)）
- **会话分支**（DeepSeek 风格）：重新输入、重新生成、分支切换 + Git 工作区 checkpoint（见 [docs/SESSION_BRANCH.md](docs/SESSION_BRANCH.md)）
- **账号体系**：无门槛注册登录，JWT 鉴权，按用户隔离数据
- **文件存储**：`data/` 目录 JSON 明文持久化（见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)）
- **管理后台**（`/admin.html`）：站点设置、统一 LLM API、用户与会话管理
- **封闭测试**：官方测试站可选横幅与注册协议（见 [docs/CLOSED_BETA.md](docs/CLOSED_BETA.md)）

## 前置条件

| 组件 | 用途 |
|------|------|
| Python 3.11+ | 后端 |
| Java JDK 17+ | Gradle 编译模组 |
| Git | 会话分支 / 工作区 checkpoint |
| Claude Code CLI | 真实 Agent 编写（可用 `USE_MOCK_SESSIONS=true` 跳过） |
| Playwright Chromium | Fabric 模板 bootstrap（`playwright install chromium`） |

## 快速启动

### Windows（推荐）

```powershell
.\scripts\setup.ps1 -AdminEmail your@email.com
.\scripts\run.ps1
```

也可双击 `server/start.bat` 启动服务。

### 手动安装

```bash
cd server
pip install -r requirements.txt
playwright install chromium
cp ../.env.example ../.env
# 编辑 JWT_SECRET、MCMOD_BOOTSTRAP_ADMIN_EMAIL
python main.py
```

访问 http://127.0.0.1:8000/

1. 在 `.env` 设置 `MCMOD_BOOTSTRAP_ADMIN_EMAIL` 为你的邮箱
2. 注册 → 登录 → 在 `/admin.html` 配置统一 LLM API
3. 首页「开始编写」创建会话

## 目录结构

```
Moduscript/
├── index.html, session.html, login.html, register.html
├── admin.html, settings.html
├── css/styles.css
├── js/                    # 前端 ES 模块
├── docs/                  # 部署、SDK、会话分支等文档
├── data/                  # 运行时数据（gitignore）
└── server/
    ├── main.py
    ├── session_service.py
    └── agent/
```

## 环境变量

见 [`.env.example`](.env.example)。关键项：

| 变量 | 说明 |
|------|------|
| `JWT_SECRET` | JWT 签名密钥 |
| `MCMOD_BOOTSTRAP_ADMIN_EMAIL` | 首注册管理员邮箱 |
| `MOD_TEMPLATE_PACKAGE` | 默认 Java 包名前缀（如 `com.example`） |
| `USE_MOCK_SESSIONS` | `true` 时使用内存 mock，无需 Claude CLI |
| `MCMOD_DATA_DIR` | 数据目录，默认 `./data` |

## 文档

- [docs/README.md](docs/README.md) — 文档索引
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 部署与管理员初始化
- [docs/AGENT_SDK.md](docs/AGENT_SDK.md) — Claude SDK 集成
- [docs/PLAN_MODE.md](docs/PLAN_MODE.md) — 规划模式
- [docs/SESSION_BRANCH.md](docs/SESSION_BRANCH.md) — 会话分支
- [deploy/README.md](deploy/README.md) — TCP 持续部署（可选）

## 贡献与安全

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
- [CHANGELOG.md](CHANGELOG.md)

## 许可证

本项目采用 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）。  
网络部署修改版须向用户提供对应源代码。详见 [docs/LEGAL_NOTES.md](docs/LEGAL_NOTES.md)。

## 免责声明

非 Minecraft 官方产品。未获 Mojang 许可，亦与 Mojang 无任何关联。
