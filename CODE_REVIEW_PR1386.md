# PR #1386 代码审查报告

## Overview

- **PR**: [#1386](https://github.com/xorbitsai/xagent/pull/1386)
- **标题**: `feat: auto-opening DAG execution progress panel`
- **作者**: `yiboyasss`
- **基线 / Head**: `12947b49a7e2eba4e83028890ba1ed851d7532a` / `57ef66b2ff916453228957b53b151a1caf6cb8f4`
- **审查结论**: `Blocking: yes`；建议事件：`REQUEST_CHANGES`

### PR 描述摘要

PR 新增右侧 DAG Progress 面板：进入 `think`/DAG 模式时自动打开，展示步骤列表、实时或冻结的耗时，并支持独立折叠和点击步骤滚动到 trace。PR 同时尝试修复跨任务 WebSocket 事件泄漏、非 DAG 任务错误构造 `DAGExecution`、`created_at` 缺失、任务切换/重连/发送消息时的陈旧 DAG 状态，以及时间戳和 stale DOM 定位问题。

PR 描述中的测试计划声称：

- `npm run type-check` / `npm run lint` 通过；
- 浏览器手工验证自动打开、计时、完成后冻结、非 DAG 模式不显示面板；
- 手工验证任务切换、重连和中途澄清输入场景。

### 变更文件

| 文件 | 变更 |
|---|---:|
| `frontend/src/app/task/[id]/page-client.tsx` | +149 / -34 |
| `frontend/src/components/chat/TraceEventRenderer.tsx` | +1 / -0 |
| `frontend/src/components/task/progress-panel.tsx` | +285 / -0 |
| `frontend/src/contexts/app-context-chat.test.tsx` | +84 / -0 |
| `frontend/src/contexts/app-context-chat.tsx` | +217 / -39 |
| `frontend/src/i18n/locales/en.ts` | +7 / -0 |
| `frontend/src/i18n/locales/zh.ts` | +7 / -0 |
| **合计** | **+750 / -73** |

## 审查方法与门禁

- 已获取 PR head、基线提交、完整提交历史和完整语义 diff。
- 已检查历史 review 与 inline comments，并对当前 head 逐项复核历史发现。
- CI 门禁：preflight 显示所有检查成功，未发现失败或进行中的检查；PR 当前 review decision 为 `CHANGES_REQUESTED`。
- 按 PR review 工作流未在本地运行测试、构建或 lint；以下结论基于代码、调用链、协议类型、测试代码和 CI 状态。

## Readability / Quality findings

### [major] DAG 运行身份没有一等模型，状态边界依赖启发式

- **位置**: `frontend/src/contexts/app-context-chat.tsx:2503-2509`；相关类型位于 `845-850`，计划事件位于 `2785-2822`，发送后 reset 位于 `5929-5961`。
- **状态**: **NOT FIXED（prior）**。
- **问题**: `DAGExecution` 没有稳定的 `run_id`/turn id。当前逻辑保留旧的 `created_at`，再按 step id 合并新事件；后端 DAG 事件和历史回放也没有可供前端校验的运行身份。新一轮事件在发送确认前到达时，发送后的对象引用判断还可能跳过 reset，导致旧轮次的步骤时间戳和新轮次的步骤混合。
- **当前代码**:

  ```ts
  const dagCreatedAt =
    currentState.dagExecution?.created_at
    ?? eventData.created_at
    ?? message.timestamp
  dispatch({
    type: "SET_DAG_EXECUTION",
    payload: { ...eventData, created_at: dagCreatedAt } as DAGExecution,
  })
  ```

- **建议**: 在 DAG producer、WebSocket envelope、历史记录、replay 和 `DAGExecution` 中统一传递稳定的 `run_id`/turn id。Reducer 只接受当前 `{taskId, runId}` 的事件，并以 run id 切换整个 `DagRunState`；不要用 `created_at`、对象引用或发送时序推断运行边界。
- **影响**: 同一任务多轮执行、replan、历史回放和快速发送场景可能显示错误的总耗时、旧步骤状态，属于阻塞性正确性问题。

### [minor] DAG wire schema 与前端类型不一致，强制 cast 掩盖协议错误

- **位置**: `frontend/src/contexts/app-context-chat.tsx:845-850`、`2471-2509`、`5105-5117`。
- **状态**: **NOT FIXED（prior；核心问题为既有协议缺口，但被新面板直接暴露）**。
- **问题**: 前端把后端事件直接断言为窄的 `DAGExecution`。后端可发送 `replanning`、`completion_assessment` 等 phase，并且 DAG 事件不稳定地携带 `current_plan`、`created_at`、`updated_at`；当前只回填 `created_at`，没有统一的 ingress 校验或归一化。
- **当前类型**:

  ```ts
  interface DAGExecution {
    phase: "planning" | "executing" | "completed" | "failed"
    current_plan: Record<string, unknown>
    created_at: string | number
    updated_at: string | number
  }
  ```

- **建议**: 定义实际 wire schema 和完整 phase union，在单一 ingress 位置完成 runtime 校验与归一化，再把内部 state 类型交给页面、图视图和 ProgressPanel。

## Defects / Correctness findings

### [major] 自动打开的固定右栏仍会破坏 tablet / narrow-desktop 布局

- **位置**: `frontend/src/app/task/[id]/page-client.tsx:109`、`173-182`。
- **状态**: **PARTIAL（prior）**。
- **问题**: 更新把 `md` 以下改为堆叠，解决了 320/640px 下固定 360px 横向挤压；但 `md:w-[360px]` 在 tablet 和窄桌面仍固定占用 360px。此时聊天列变窄，已有 `TaskConversationPanel` 的 PreviewSheet 仍按 50/50 分割，标题和操作按钮未换行；在更窄屏幕上，`h-[45vh]` 又会压缩聊天高度，而既有图区域有 `min-h-[500px]`，组合场景仍可能被裁剪。
- **当前布局**:

  ```tsx
  <div className="h-full flex flex-col md:flex-row bg-background">
    ...
    <div className="w-full md:w-[360px] shrink-0 h-[45vh] md:h-full ...">
      <ProgressPanel ... />
    </div>
  </div>
  ```

- **建议**: 在布局边界根据可用宽度计算 rail，或在受限宽度使用 drawer/overlay/auto-collapse；同时与 PreviewSheet 协调最小可用聊天宽度。至少补充 768px、1024px、1280px 和 PreviewSheet 同时打开的行为/视觉验证。
- **影响**: DAG 任务默认自动打开面板，因此该布局回归是默认路径，不是仅在用户主动打开后的样式问题。

### [minor] 数字 `0` 时间戳仍会被共享 normalizer 当作当前时间

- **位置**: `frontend/src/components/task/progress-panel.tsx:45-47`、`77-95`、`192-207`；共享 helper：`frontend/src/lib/time-utils.ts:34-35`。
- **状态**: **PARTIAL（prior）**。
- **问题**: 面板已用显式存在性判断识别数字 `0`，但 `normalizeTimestampMs` 仍使用 truthiness 判断，导致数字 `0` 返回 `Date.now()`；字符串 `"0"` 则走另一条路径并保留 epoch。相同 API 的两种表示产生不同耗时。
- **当前代码**:

  ```ts
  function hasTimestamp(value: string | number | undefined) {
    return value !== undefined && value !== null && value !== ""
  }
  // frontend/src/lib/time-utils.ts
  if (!ts) return Date.now()
  ```

- **建议**: 将共享 normalizer 改为区分 `null`/`undefined`/空字符串和合法数字 0，并明确无效数字/字符串的行为。补充数字 `0`、字符串 `"0"`、空值和非法值的边界测试。

### [minor] `interrupted` / `clarification_invalidated` 状态被折叠为 `pending`

- **位置**: `frontend/src/contexts/app-context-chat.tsx:728-757`、`820-841`；`frontend/src/components/task/progress-panel.tsx:13`、`105-107`、`228-250`。
- **状态**: **NOT FIXED（prior；既有状态缺口被新面板直接暴露）**。
- **问题**: 后端 PlanStep 会发送 `interrupted` 和 `clarification_invalidated`，但共享 normalizer 只保留五种状态，其余状态转成 `pending`。ProgressPanel 的 status union、图标、样式和 resolved count 也没有这两种状态；尤其 `clarification_invalidated` 是非终态，简单按 pending 展示会丢失用户可见的生命周期信息。
- **当前代码**:

  ```ts
  const normalizeStepStatus = (status: unknown): StepExecution["status"] => {
    if (status === "running" || status === "completed" || status === "failed" || status === "skipped") {
      return status
    }
    return "pending"
  }
  ```

- **建议**: 保留完整后端状态枚举，明确两种状态的显示、是否终态、是否计入进度的产品语义；不要在没有协议决策时把 `clarification_invalidated` 当作 resolved。

## Security findings

未发现新增安全问题。改动路径使用 React 文本渲染步骤标题/描述，没有新增 `dangerouslySetInnerHTML`、动态脚本、鉴权绕过或 secrets 处理；文件预览的跨任务功能路径已通过 `OPEN_FILE_PREVIEW` task-scoped guard 修复。新测试 fixture 不准确是测试质量问题，不是当前可利用的跨任务文件预览漏洞。

## Performance findings

未发现当前仍开放的性能/资源问题。此前每个 running row 建立独立 interval 的 N+1 问题已修复：`ProgressPanel` 现在只建立一个 panel-level `setInterval`，rows 共享同一时间值，并在 unmount/结束时清理。DOM 查询只在步骤点击时执行，没有发现新的高频全量扫描。

## Best-practices / Test findings

### [minor] DAG 面板和生命周期行为测试覆盖不足

- **位置**: `frontend/src/contexts/app-context-chat.test.tsx:1429-1501`；生产路径 `frontend/src/contexts/app-context-chat.tsx:2477-2510`、`5929-5961`，`frontend/src/app/task/[id]/page-client.tsx:37-59`、`95-106`，`frontend/src/components/task/progress-panel.tsx:69-209`。
- **状态**: **PARTIAL（prior）**。
- **问题**: 当前测试覆盖了跨任务 guard 和非 DAG `task_completed` 不构造 DAG 的行为，但没有覆盖 `created_at` backfill、发送成功/失败及任务切换 reset、自动打开/手动折叠/结束冻结和 ProgressPanel 组件生命周期。新增的 `task_completed` 预览 fixture 也不是实际 normalized envelope：缺少生产中的 `data` 包装，并使用会在 `normalizeGeneratedPreviewFiles` 中被丢弃的字符串 `file_outputs`，所以删除 guard 后测试仍可能保持绿色。
- **当前测试片段**:

  ```tsx
  onMessage?.({
    type: "task_completed",
    task_id: 2,
    task: { id: 2, status: "completed" },
    file_outputs: ["report.pdf"],
  })
  ```

- **建议**: 使用生产 normalized 形状（`data` 包含原始 completion payload，`file_outputs` 使用带 `file_id` 的对象）验证预览 guard；增加可控时钟下的 backfill、reset、auto-open、dismiss、freeze 和 row duration 测试，并覆盖发送异常不应清除已完成 DAG 的路径。

## Severity-ranked summary

| 排名 | 严重级别 | 状态 | 文件:行 | 结论 |
|---:|---|---|---|---|
| 1 | major | NOT FIXED | `frontend/src/contexts/app-context-chat.tsx:2503` | 无稳定 DAG run identity，多轮/replay 可能合并状态 |
| 2 | major | PARTIAL | `frontend/src/app/task/[id]/page-client.tsx:174` | 自动打开的固定右栏仍破坏 tablet / narrow-desktop 组合布局 |
| 3 | minor | PARTIAL | `frontend/src/components/task/progress-panel.tsx:45` | 数字 0 被 `normalizeTimestampMs` 当作当前时间 |
| 4 | minor | NOT FIXED | `frontend/src/contexts/app-context-chat.tsx:845` | wire schema 与前端 DAGExecution 类型不一致 |
| 5 | minor | NOT FIXED | `frontend/src/components/task/progress-panel.tsx:13` | 后端 interrupted 状态无法正确显示/计数 |
| 6 | minor | PARTIAL | `frontend/src/contexts/app-context-chat.test.tsx:1429` | backfill/reset/panel 生命周期缺少有效行为测试 |

## 已验证修复与简化结果

以下历史问题已验证修复，本轮不重复报告：

- foreign `task_completed` 的 `OPEN_FILE_PREVIEW` 功能路径已被 task-scoped action guard 拦截；其测试 fixture 缺陷已并入测试覆盖问题。
- 无 trace target 的 pending/skipped 步骤现在 disabled；同任务 DAG reset 已统一为 `RESET_DAG_STATE`；N+1 interval 已改为单一 panel clock。
- `isDuplicateResult`、`isDuplicateResultForViewedTask` 及相关固定参数/重复 `updatedAt` plumbing 已清理；`onStepClick` 已改为必需属性。
- 更广泛的 imperative foreign-frame side effects 与基线代码相同，复核后按既有 N10 丢弃，不作为本 PR 新回归。
- Full-diff 和 update-only Simplification Lens 均返回 `Lean already.`。

## Overall assessment

当前实现的 UI 组件边界和单计时器方向合理，且本轮确实修复了 task-scoped 文件预览、reset 重复、N+1 timer、无目标步骤点击和多处 weightless helper 问题。但 DAG run identity 仍未建模，导致多轮/replay 状态无法可靠分区；自动打开的固定右栏仍会在已有 PreviewSheet 组合下破坏可用布局。这两个 major 问题会影响默认 DAG 使用路径，建议在合并前解决；其余 minor 问题可作为同一轮修复或后续跟踪项。

**Blocking: yes — recommended event: `REQUEST_CHANGES`.**
