/** @file 编写中页静态预览 / Phase 2 bootstrap 假数据 */

export const MOCK_TASK_TITLE = '末影珍珠诅咒改造';

export const MOCK_FINAL_PROMPT = `# Minecraft 模组生成请求

<project_constraints>
  <task_type>mod</task_type>
  <minecraft_version>1.21.1</minecraft_version>
  <loader api="neoforge">NeoForge</loader>
  <deployment>客户端与服务端均必装</deployment>
</project_constraints>

## 模组构想
https://modrinth.com/mod/immersivecursedness
参考该模组复刻末影珍珠诅咒机制：投掷后不立即传送，落地产生诅咒区域，传送带冷却与虚弱代价。

## 其他要求
### 生产级架构质量
模组按生产级标准构建，保证清晰的模块边界、可维护性与可测试性。

### 充足配置项
通过配置文件（如 common/client 分离）暴露充足可调参数，便于玩家与整合包作者自定义。

## 参考模组
### 参考功能
- **Immersive Cursedness**
  - 链接：https://modrinth.com/mod/immersivecursedness
  - 说明：诅咒区域与渐进式 debuff 机制参考`;

export const MOCK_READABLE_BLUEPRINT = `## 末影珍珠诅咒改造

### 核心机制
- 拦截末影珍珠落地，**延迟传送**而非即时生效
- 落点生成**诅咒区域**（范围效果 + 粒子）
- 传送后施加短暂**虚弱** debuff 作为代价
- 可配置**冷却时间**（默认 2 秒 / 40 tick）

### 环境与约束
- NeoForge **1.21.1**，客户端与服务端均必装
- 参考 [Immersive Cursedness](https://modrinth.com/mod/immersivecursedness) 的区域诅咒思路
- 生产级模块划分 + 充足配置文件（common / client 分离）

### 首版交付范围（MVP）
1. 事件拦截与延迟传送
2. 诅咒区域基础效果
3. 配置项：半径、持续时间、冷却、debuff 等级`;

export const SESSION_STORAGE_BOOTSTRAP = 'moduscript_session_bootstrap';
