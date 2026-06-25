# 参与贡献

感谢你对 Moduscript 的关注。

## 开发环境

### Windows

```powershell
.\scripts\setup.ps1 -AdminEmail your@email.com
.\scripts\run.ps1
```

### 手动安装

```bash
cd server
python -m venv ../.venv
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
cp ../.env.example ../.env
python main.py
```

详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 与 [docs/LINUX_MIGRATION.md](docs/LINUX_MIGRATION.md)。

## 前置条件

- Python 3.11+
- Java JDK 17+（Gradle 编译）
- Git（会话分支 / 工作区 checkpoint）
- [Claude Code CLI](https://code.claude.com/)（可选；`USE_MOCK_SESSIONS=true` 时可跳过）

## 运行测试

从源码克隆后（含 `server/test_*.py`）：

```bash
cd server
pip install -r requirements-dev.txt
pytest -v
```

## 提交 Pull Request

1. Fork 后从 `main` 创建功能分支。
2. 保持改动聚焦，遵循现有代码风格。
3. 提交前尽量运行 `pytest`。
4. **不要提交：** `.env`、`data/`、`deploy/keys/psk.hex`、`deploy/config/sender.json` 或调试日志。
5. 用户可见的变更请更新 [CHANGELOG.md](CHANGELOG.md)。
6. 行为或 API 变化时，同步更新 `docs/` 下相关文档。

## 安全

漏洞请通过 [SECURITY.md](SECURITY.md) 报告，勿在公开 Issue 中披露未公开的安全问题。

## 许可证

参与贡献即表示你同意将贡献内容以 [GNU AGPL-3.0](LICENSE) 授权。
