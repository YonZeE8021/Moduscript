# 测试服务器手动搭建指南

> Moduscript 网页端的「启动测试服务器」功能在封闭测试阶段为**占位功能**。请按本文在本地或局域网手动验证生成的模组 jar。

## 前置条件

1. 在编写会话中完成构建，并通过「下载模组 jar」获取产物（或从 `data/workspaces/{user_id}/{session_id}/build/libs/` 取 jar）。
2. 安装对应 MC 版本与加载器的运行环境（NeoForge / Fabric / Forge 等）。

## NeoForge / Forge 客户端测试

1. 安装对应版本的 Minecraft 启动器（如 Prism Launcher、HMCL）。
2. 创建实例，安装与任务一致的 MC 版本与 NeoForge/Forge。
3. 将下载的 `.jar` 放入实例的 `mods` 文件夹。
4. 启动游戏验证功能。

## 专用服务器（局域网）

1. 从 [NeoForge](https://neoforged.net/) 或 [Fabric](https://fabricmc.net/) 下载对应版本服务端安装包。
2. 首次运行生成 `mods` 目录，将模组 jar 放入其中。
3. 接受 EULA（`eula.txt` 中 `eula=true`）。
4. 启动服务端；客户端通过 `localhost` 或局域网 IP 连接。

## 常见问题

| 问题 | 建议 |
|------|------|
| jar 为占位文件 | Agent 尚未完成 Gradle 构建；查看会话日志与 workspace 目录 |
| 版本不匹配 | 确认任务稿中的 MC 版本、加载器与运行环境一致 |
| 缺少依赖 mod | 根据 `mods.toml` / `fabric.mod.json` 安装依赖 |

## 后续计划

自动测试服务器（Docker 化 MC 实例、一键开服）将在封闭测试稳定后单独迭代，详见项目 Roadmap。
