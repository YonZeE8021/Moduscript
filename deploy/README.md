# Moduscript TCP 持续部署

开发机改完代码后，一条命令将增量更新推送到目标服务器。传输使用自定义 **MDPL** TCP 帧协议，可经 **frp `type=tcp`** 穿透；接收端自动 `pip install` 并重启服务。

---

## 快速开始

### 1. 目标机（生产环境）

1. 拷贝整个项目到目标目录
2. 运行 `.\scripts\setup.ps1` 初始化 venv（会自动从 example 复制 deploy 密钥与 sender 配置）
3. 配置 `.env`（部署**不会**覆盖此文件）
4. **双击或运行项目根目录 [`Moduscript.bat`](../Moduscript.bat)**

   一个窗口同时包含 deploy receiver 与 Web 服务；部署后自动重启 integrated 模式。

   Linux：`chmod +x Moduscript.sh && ./Moduscript.sh`

   **本地开发**仍用 [`scripts/run.ps1`](../scripts/run.ps1)（仅 Web 服务，无 deploy receiver）。

5. 配置 frp，见 [`frp/frpc.example.ini`](frp/frpc.example.ini)

### 2. 开发机（日常部署）

1. 从 [`config/sender.example.json`](config/sender.example.json) 复制为 `config/sender.json`
2. 从 [`keys/psk.hex.example`](keys/psk.hex.example) 生成真实密钥写入 `keys/psk.hex`（两端一致）
3. 推送：`deploy\deploy.bat` 或 `python deploy/cli.py deploy`

## 密钥

预共享密钥位于 `keys/psk.hex`（**不提交 Git**）。生成：

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

## frp 穿透

见 [`frp/frpc.example.ini`](frp/frpc.example.ini)。须使用 `type = tcp`。

## 同步范围

**包含：** `server/`、`*.html`、`css/`、`js/`、`docs/`、`scripts/`、`.env.example`、`README.md`

**永不覆盖：** `data/`、`.env`、`.venv/`、`deploy/keys/psk.hex`

## 故障排查

| 现象 | 处理 |
|------|------|
| 连接超时 | 检查 frpc、`sender.json` 地址/端口、防火墙 |
| HMAC verification failed | 两端 `psk.hex` 不一致 |
| pip install failed | 目标机先运行 `setup.ps1` |
| 重启后服务未起来 | 确认用 **Moduscript.bat** 启动 |

更多细节见 [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)。
