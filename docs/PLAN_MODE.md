# 规划模式（Plan Mode）

## 功能概述

规划模式是独立于「编写会话」的需求细化流水线：

1. 用户在首页填写 Mod 构想，点击「开始规划」
2. 首次使用需完成 L1 四维知识问卷（可缓存；可在 `/settings.html` 修改）：**第一次点击会弹出问卷并提示，确认后才跳转**；已保存 L1 的用户一键进入
3. 若使用「优化描述」且优化面板处于展开状态，**以优化结果的字数**判定「开始规划」是否可点（与提交到规划页的构想文本一致）
4. LLM 多轮结构化问答（选项 + 自定义 + 开放式提问 + 自由回复）
5. 右侧方案树实时更新（列表 / D3 导图双视图）
6. 定稿为 Markdown，handoff 到编写会话（含构想页需求、参考、高级设置附录）

## Handoff 到编写会话

规划定稿后点击「结束规划并编写」，服务端创建编写会话并注入与首页「开始编写」对齐的任务 prompt。

### Prompt 结构（`_build_handoff_prompt`）

```
<task>
  <project_constraints>…</project_constraints>   <!-- 版本、加载器、mod 元数据、语言 -->
  <user_concept>…</user_concept>
  <reference_context>…</reference_context>       <!-- 可选：Reference Card + research findings -->
  <plan_summary>
    {定稿 Markdown}
  </plan_summary>
  {handoff_appendix}                             <!-- 其他要求、参考模组、高级设置等 -->
</task>
```

### 构想附录（`handoff_appendix`）

| 来源 | 说明 |
|------|------|
| 创建规划时 `context.handoff_appendix` | 前端 `js/app.js` 调用 `buildHandoffAppendix(promptCtx)`，与编写模式 `buildFinalPrompt` 附录同源（`js/prompt-builder.js`） |
| 无 appendix 的旧规划 | `build_handoff_appendix_fallback(context)` 从 `requirements` / `reference_mods` 等字段近似还原 |

这样 **「开始规划 → 定稿 → handoff」** 与 **「开始编写」** 在需求、参考模组、其他要求、高级设置（`max_turns`、`interruption_level` 等）上保持一致，避免 handoff 丢失构想页信息。

### Handoff 侧车逻辑

- 复制 `data/plans/{user_id}/{plan_id}/references/{slug}/` → 编写工作区 `references/`
- `payload.handoff_plan_id` 关联来源规划；编写 bootstrap 可跳过重复 materialize
- 每次 handoff 均创建新的编写会话；旧会话保留不变

`PlanContext` 扩展字段见 `server/plan/schemas.py`：`handoff_appendix`、`mod_name`、`mod_id`、`package_name`、`max_turns`、`interruption_level` 等。

## 页面与入口

| 页面 | 路径 | 说明 |
|------|------|------|
| 首页 | `/index.html` | 「开始规划」入口 |
| 规划页 | `/plan.html?plan_id=...` | 多轮问答、方案树、定稿 |
| 用户设置 | `/settings.html` | L1 知识水平调整 |
| 管理后台 | `/admin.html` | LLM 配置（Tab 切换） |

新建规划后首轮 LLM 生成期间会显示「处理中」，属正常等待而非失败；参考索引完成不会打断该状态。

## UX 迭代（2025-06）

### 选项复杂度 → 背景色

- 每个选项行使用 **绿 / 黄 / 红** 淡色背景 + 左侧 3px 色条表示低 / 中 / 高复杂度
- 不再显示「复杂度：低/中/高」文字 badge
- LLM schema 字段仍为 `options[].complexity`

### 流式反馈（选项区 / 定稿预览）

- **提交本轮回答**、**首次生成**、**bootstrap** 时：问卷区立即清空，在 `#planQuestions` 显示原始 JSON 流（`plan-llm-stream`）
- **结束规划并编写**：modal 内 `#planFinalizePreview` 显示 Markdown 原始流
- 后端 `submit_turn` / `finalize` 改为后台任务，HTTP 立即返回 `processing=true`
- SSE 事件：
  - `llm_delta` — 出题 JSON 流
  - `finalize_delta` — 定稿 Markdown 流
  - `finalize_ready` — 定稿完成
  - `turn_ready` — 本轮出题完成

### D3 可缩放方案树

- 规划页引入 D3 v7（CDN，仅本页）
- 方案树 panel 顶栏可切换 **列表 | 导图**
- 导图支持拖拽平移、滚轮缩放；节点点击在**节点旁 Popover** 显示 summary（非右下角 toast）

### 参考源码渐进披露（L0–L3）

| 层级 | 内容 |
|------|------|
| L0 | 每轮 prompt 注入 requirements、reference_mods 元数据；规划页底部脚注只读展示 |
| L1 | 创建规划后后台浅 clone（需 `source_url`）或闭源反编译（需 `decompile_attempt`）→ `index.json` + LLM Reference Card |
| L2 | turn JSON 可选 `source_lookups`；手动 `POST .../lookup` 检索 snippet → `research_findings` |
| L3 | handoff 复制 `references/{slug}/` 到编写工作区；prompt 含 findings 摘要 |

**闭源反编译（Fabric）**：用户勾选「尝试反编译」且无 `source_url` 时，后台从 Modrinth 下载 jar → Tiny Remapper + Yarn → Vineflower → `repo/` + 索引。Yarn 重映射失败时降级为 `decompiled_obfuscated`（仍索引混淆反编译树）。完全失败时由规划 LLM 生成 `metadata_only` Reference Card（不伪造 `repo/`）。

**依赖**：本机 Java 17+；反编译工具（Vineflower / tiny-remapper）首次索引或 `setup.ps1` 时**自动下载**到 `data/tools/`。

无 `source_url` 且未勾选反编译时，仅 L0 元数据。

**首轮出题时序**：有代码参考时默认**等待索引完成**再生成首轮问题（等待期间主区展示索引/反编译工具步骤）；可点击「暂时跳过索引，先进行首轮规划」。无代码参考时立即出题。

**clone/反编译失败不阻塞规划**（跳过或终态失败后仍可继续）；`reference_index` 标记 `failed` 时可「重试索引」。materialize 仅 patch 参考字段，避免覆盖 `turns`。

### 对话列表统一

- 编写页侧栏点击「规划」正确跳转 `plan.html`（修复 `[object Object]`）
- 规划页显示「构想」条目并支持新建构想
- 规划项支持 **置顶 / 重命名 / 移至回收站 / 恢复**（`PATCH`、`POST .../trash`、`POST .../restore`；列表 `?recycled=true`）

### 页底只读脚注

- 「已选要求 / 参考模组」与「参考源码索引」移至 `plan-main` 底部 `.plan-page-footer`
- 小字只读展示，无 `<details>` 折叠与 lookup 输入；内容全展开，随页面自然滚动
- 页脚不展示 Reference Card 正文，仅展示索引状态、文件数/入口路径摘要与已检索片段；Reference Card 仅注入 LLM prompt

### 问卷操作区布局

- 「重新生成本轮问题」按钮右对齐（`.plan-question-actions-right`）

### D3 导图 Popover 与缩放

- 双层 `g`（zoom + layout）避免缩放丢失布局平移
- Popover 使用 `clientX/clientY` + `position: fixed`
- 初始 fit 缩放 + 动态 `scaleExtent`；双击重置 fit

### 方案树布局

- history/tree panel **固定高度** + `scrollbar-gutter: stable`，避免列表出现滚动条时整页布局跳动

### SSE 连接稳定性

- 规划页各 panel 统一 `overflow-x: hidden`、`min-width: 0`
- 对话历史回复使用人类可读格式（选项 label 映射），长文本自动换行

### 问题 / 选项重新生成

- 当前轮 **未提交** 且未 processing 时可用
- 单题：**扩增选项**（expand）/ **重新生成**（replace）
- 整轮：**重新生成本轮问题**；开始前将方案树回滚至基线（首轮为默认树，后续轮为上一轮 snapshot），避免草稿节点误导 LLM
- 整轮重生成支持 **温度** 输入（0–2，默认 0.5，localStorage 记忆）；body: `{ instruction?, temperature? }`
- API 见下方 REST 表

### 问卷交互

- **单选**再次点击已选项可取消选择
- 提交时若有未选题，弹出确认后可强制提交
- **自由回复** focus 使用 inset 高亮，避免边框在容器外溢出
- **开放式提问**：LLM 输出 `open_questions`（0–3 条不宜选项化的追问），在「自由回复」上方以 `<details>` 默认折叠展示；用户仍在「自由回复」（`overall_remarks`）中作答，无需额外 API 字段

### 「整体备注」→「自由回复」

- UI 文案改为「自由回复」；API 字段 `overall_remarks` 不变

### Admin LLM → Tab 切换卡

- 管理后台 LLM 配置由导航卡片改为 Tab：**统一 LLM | 描述优化 | Mod 名称 | 规划 LLM**
- Tab 徽章反映各配置项状态（已配置 / 未配置 / 已禁用）

### SSE 连接稳定性

- 前端使用 `planEventsCleanup` 替代 `disconnectEvents`，赋值前校验类型
- 提交 / 保存 L1 后不再重复 `setupEvents()` 重连，保持单 SSE 订阅

## Admin LLM 配置说明

| 配置项 | 用途 | 存储文件 |
|--------|------|----------|
| 统一 LLM API | 编写 Agent（站点开启 shared_llm 时） | `data/admin/llm_shared.json` |
| 描述优化 | 首页「优化描述」 | `data/admin/prompt_optimize.json` |
| Mod 名称生成 | 开始编写时自动生成名称 | `data/admin/mod_name_suggest.json` |
| 规划模式 LLM | 多轮问答 + 定稿 Markdown | `data/admin/plan_llm.json` |

未配置或调用失败时，规划页显示 `last_error` 错误横幅与重试按钮，不会注入演示问卷；请在管理后台配置规划 LLM API Key。

## API 与 SSE 事件

### REST

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/plan/sessions` | 规划列表（`?recycled=true` 回收站） |
| PATCH | `/api/v1/plan/sessions/{id}` | 更新标题 / 置顶（body: `{ task_title?, pinned? }`） |
| POST | `/api/v1/plan/sessions/{id}/trash` | 移至回收站 |
| POST | `/api/v1/plan/sessions/{id}/restore` | 从回收站恢复 |
| POST | `/api/v1/plan/sessions` | 创建规划 |
| GET | `/api/v1/plan/sessions/{id}` | 获取快照 |
| POST | `/api/v1/plan/sessions/{id}/turns` | 提交本轮回答（后台 LLM，立即返回） |
| POST | `/api/v1/plan/sessions/{id}/turns/retry-first` | 空轮次时重试首轮出题 |
| POST | `/api/v1/plan/sessions/{id}/turns/regenerate` | 整轮重新生成（body: `{ instruction?, temperature? }`，temperature 0–2） |
| POST | `/api/v1/plan/sessions/{id}/turns/questions/{qid}/regenerate` | 单题扩增/重生成（body: `{ action: "expand" \| "replace" }`） |
| POST | `/api/v1/plan/sessions/{id}/finalize` | 定稿 Markdown（后台，立即返回） |
| POST | `/api/v1/plan/sessions/{id}/handoff` | 移交编写会话（复制 references/ 到工作区） |
| GET | `/api/v1/plan/sessions/{id}/references` | 索引状态、cards、findings |
| POST | `/api/v1/plan/sessions/{id}/references/{pid}/lookup` | 手动检索参考源码 |
| GET | `/api/v1/plan/sessions/{id}/events` | SSE 事件流 |

### SSE 事件类型

| 类型 | 说明 |
|------|------|
| `snapshot` | 完整规划快照 |
| `processing` | 开始处理（出题/定稿/重生成） |
| `llm_delta` | LLM 流式 token（`data.text`，出题 JSON） |
| `finalize_delta` | 定稿 Markdown 流（`data.text`） |
| `turn_ready` | 本轮 LLM 完成 |
| `finalize_ready` | 定稿完成 |
| `reference_indexing` | 开始索引参考模组 |
| `reference_ready` / `reference_failed` | 单 mod 索引完成/失败 |
| `research_finding` | 新增检索片段 |
| `error` | 错误信息 |
| `ping` | 保活 |

## 手动验证步骤

1. **提交回答**：选项区立即显示 JSON 流，完成后渲染新问卷
2. **选项**：绿/黄/红背景色可见，无复杂度文字标签
3. **定稿**：modal 内 Markdown 流式预览，完成后渲染
4. **方案树**：列表 / 导图切换，D3 初始 fit、滚轮缩放、节点点击 Popover 位置正确
5. **侧栏**：规划项置顶/重命名/回收站/恢复
6. **页脚**：要求与参考信息在页面底部只读展示
7. **重生成**：未回答时可扩增/重生成单题或整轮；整轮重生成后方案树无草稿残留
8. **问卷**：单选可取消、未答 confirm 提交、温度输入生效；有 `open_questions` 时折叠面板与 placeholder 正常
9. **Admin**：四 Tab 切换与徽章正常
10. **Handoff 附录**：定稿 handoff 后编写会话 `final_prompt` 在 `</plan_summary>` 之后含其他要求 / 参考 / 高级设置（与「开始编写」一致）
11. **回归**：`cd server && py -3 -m pytest test_plan_flow.py test_plan_prompts.py -v`

## 相关源码

| 模块 | 路径 |
|------|------|
| 后端服务 | `server/plan/service.py` |
| LLM | `server/plan/chat_llm.py` |
| Prompt / handoff 附录 | `server/plan/prompts.py`（`build_handoff_appendix_fallback`） |
| Handoff 编排 | `server/plan/service.py`（`_build_handoff_prompt`、`_resolve_handoff_appendix`） |
| 构想附录（前端） | `js/prompt-builder.js`（`buildHandoffAppendix`） |
| 规划页 | `js/plan-preview.js`, `plan.html` |
| 流式面板 | `js/plan-stream-panel.js` |
| 参考索引 | `server/plan/reference_index.py` |
| Reference Card | `server/plan/reference_card.py` |
| 节点 Popover | `js/plan-node-popover.js` |
| 问题表单 | `js/plan-question-form.js` |
| 首页入口 | `js/app.js`（bootstrap 跳转） |
