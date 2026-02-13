# Multi-Agent Orchestration 状态接口接入说明（run_id 生产版）

本文档说明如何在 `multi-agent-orchestration` 侧接入 Codex TUI 暴露的 orchestration 状态接口，并给出一份“多维度检测”结果。

---

## 1. 接口目标

让 orchestration 流程可以主动控制底部状态条：

- orchestration 执行中：显示运行状态（如 `Running targeted tests`）
- orchestration 空闲：状态条仍然可见，不再隐藏，默认显示 `等待指示`
- orchestration 并发执行：基于 `run_id` 跟踪，不再依赖单布尔位

---

## 2. 接口定义

### 2.1 推荐接口（run_id 生命周期）

```rust
AppEvent::BeginOrchestrationTaskState {
    run_id: String,
    status_header: Option<String>,
    status_details: Option<String>,
}

AppEvent::UpdateOrchestrationTaskState {
    run_id: String,
    status_header: Option<String>,
    status_details: Option<String>,
}

AppEvent::EndOrchestrationTaskState {
    run_id: String,
}
```

字段语义：

- `run_id`：任务唯一标识；空串会回落到 legacy run_id
- `status_header`：状态标题（非空才更新）
- `status_details`：状态详情（非空才更新）

### 2.2 兼容接口（保留）

```rust
AppEvent::SetOrchestrationTaskState {
    running: bool,
    status_header: Option<String>,
}
```

兼容接口会映射到内部 legacy run_id，便于旧调用方平滑迁移。

### 2.3 可选稳定性提示接口（iTerm 绑定告警）

```rust
AppEvent::SetOrchestrationBindingWarning {
    warning: Option<String>,
}
```

字段语义：

- `warning`：`Some(text)` 设置告警；`None` 清空告警
- 告警仅在 orchestration 成为当前展示源时附加到 status details（不抢占 core turn / MCP 展示）

---

## 3. 调用链（代码落点）

1) 事件定义：
- `/Users/mima0000/Desktop/wj/codex/codex-rs/tui/src/app_event.rs`

2) App 层分发到 ChatWidget：
- `/Users/mima0000/Desktop/wj/codex/codex-rs/tui/src/app.rs`

3) ChatWidget 接口与聚合状态逻辑：
- `/Users/mima0000/Desktop/wj/codex/codex-rs/tui/src/chatwidget.rs`

4) BottomPane 状态计时器控制：
- `/Users/mima0000/Desktop/wj/codex/codex-rs/tui/src/bottom_pane/mod.rs`

---

## 4. 运行态聚合规则

TUI 最终运行态是三路 OR：

```text
running = agent_turn_running
       || mcp_startup_status.is_some()
       || !orchestration_task_states.is_empty()
```

即：
- 任一路在运行，状态条按运行态展示
- 三路都空闲，进入空闲态展示

---

## 5. 状态展示优先级

运行态下，展示优先级为：

1. core turn / MCP startup（核心流程）
2. orchestration（无 core turn 且无 MCP startup 时）

orchestration 侧展示策略：
- 选“最近一次更新”的 run（按内部 `last_update_seq`）作为当前展示源

---

## 6. Orchestration 侧接入步骤（推荐）

### 步骤 1：拿到 `AppEventSender`

在 orchestration 组件内保留一个可用的 `AppEventSender`（或能间接发送 `AppEvent` 的通道）。

### 步骤 2：开始执行时发送 Begin

```rust
app_event_tx.send(AppEvent::BeginOrchestrationTaskState {
    run_id: "run-20260213-001".to_string(),
    status_header: Some("Running targeted tests".to_string()),
    status_details: Some("phase=tests".to_string()),
});
```

### 步骤 3：阶段切换时发送 Update

```rust
app_event_tx.send(AppEvent::UpdateOrchestrationTaskState {
    run_id: "run-20260213-001".to_string(),
    status_header: Some("Reviewing patches".to_string()),
    status_details: Some("phase=review".to_string()),
});
```

### 步骤 4：结束/失败/取消时发送 End

```rust
app_event_tx.send(AppEvent::EndOrchestrationTaskState {
    run_id: "run-20260213-001".to_string(),
});
```

### 步骤 5：finally/defer 保证回收

无论成功、失败、取消、提前返回，都必须保证发送 End。

### 步骤 6（可选）：iTerm 绑定不稳时提示

```rust
app_event_tx.send(AppEvent::SetOrchestrationBindingWarning {
    warning: Some("iterm session rebound".to_string()),
});
```

恢复稳定后清空：

```rust
app_event_tx.send(AppEvent::SetOrchestrationBindingWarning { warning: None });
```

---

## 7. 多维度检测结果（完整）

检测时间：2026-02-13

### 维度 A：接口能力完整性
- 结果：通过
- 结论：已具备 Begin/Update/End + 兼容布尔接口

### 维度 B：并发正确性
- 结果：通过
- 结论：由 `HashMap<run_id, state>` 跟踪活跃任务，避免“某子任务提前置 false”误伤全局运行态

### 维度 C：乱序/幂等容错
- 结果：通过（带边界）
- 结论：`Update` 对未知 run_id 采用 upsert 语义，防止乱序导致状态丢失
- 边界：这会容忍生产者错误；建议调用侧仍保证 Begin -> Update* -> End

### 维度 D：向后兼容
- 结果：通过
- 结论：`SetOrchestrationTaskState` 仍可用，映射到 legacy run_id

### 维度 E：与核心状态隔离
- 结果：通过
- 结论：core turn / MCP startup 仍是主导运行源，orchestration 只在核心空闲时接管展示

### 维度 F：空闲 UX 一致性
- 结果：通过
- 结论：空闲时状态条常显、计时器暂停、中断提示关闭、标题回落 `等待指示`

### 维度 G：可观测与调试
- 结果：部分通过
- 结论：UI 层可见；但 run 列表/历史未持久化，不是跨进程状态总线
- 建议：如需跨进程可观测，仍应由 orchestration 外层落地日志/DB/SSE

### 维度 H：iTerm 绑定稳定性提示
- 结果：通过
- 结论：支持单独设置/清理 binding warning，且只在 orchestration 展示路径中可见

### 维度 I：测试覆盖
- 结果：通过
- 结论：新增/保留用例覆盖 run_id 并发、legacy 兼容、upsert 更新、空闲常显

---

## 8. 自动化测试（已验证）

已执行并通过：

```bash
cargo check -p codex-tui
cargo test -p codex-tui orchestration_
cargo test -p codex-tui task_complete_keeps_idle_status_visible
```

覆盖用例：
- `chatwidget::tests::orchestration_task_state_controls_running_and_idle_status`
- `chatwidget::tests::orchestration_run_ids_keep_running_until_all_end`
- `chatwidget::tests::legacy_orchestration_boolean_does_not_cancel_named_runs`
- `chatwidget::tests::orchestration_update_upserts_and_updates_details`
- `chatwidget::tests::orchestration_binding_warning_appends_to_details`
- `chatwidget::tests::orchestration_binding_warning_sets_details_when_empty`
- `chatwidget::tests::orchestration_binding_warning_can_be_cleared`
- `chatwidget::tests::task_complete_keeps_idle_status_visible`

说明：
- 全量 `cargo test -p codex-tui` 在当前工作区存在与本改动无关的 snapshot 漂移（`status` 快照版本头差异），不影响 orchestration 接口行为结论。

---

## 9. 生产使用规范（强约束）

1. 每个并发任务必须使用唯一 `run_id`。
2. 所有任务必须在 finally/defer 中发送 End。
3. 不要复用已结束任务的 `run_id`。
4. 建议每个阶段至少发一次 Update（便于观测）。
5. 如果仍使用 legacy 布尔接口，建议尽快迁移到 run_id 接口。

---

## 10. 已知边界与改进建议

已知边界：
- 当前 `status_header/status_details` 仅在“非空”时更新，不支持通过空串清空。
- 当前展示按“最近更新 run”策略，不提供显式优先级字段。
- 当前无 run 状态持久化（重启后丢失）。

可选改进（后续版本）：
- 新增显式 `priority` / `phase` / `progress` 字段；
- 新增只读查询接口（如导出当前 active run 概览）；
- 支持 details 清空语义（例如显式 `clear_details: true`）。

---

## 11. 快速排障

现象：状态一直 running 不回落
- 常见原因：遗漏 End
- 处理：检查调用侧是否在所有错误分支/finally 发送 End

现象：并发任务状态标题“跳来跳去”
- 常见原因：多个 run 高频 Update
- 处理：调用侧做节流（例如阶段切换时再 Update）

现象：legacy false 后仍显示 running
- 常见原因：仍有 run_id 任务活跃
- 处理：检查 active run 是否都已 End

---

## 12. 本次 TUI 验收结论（2026-02-13）

验收目标：确认“尽量不改 orchestration 主流程、由 TUI 承接状态展示与稳定性提醒”的需求是否达成。

结论：**达成（可用于生产接入）**。

逐项验收：

1) run_id 生命周期接口（Begin/Update/End）
- 结果：通过
- 证据：事件定义 + App 分发 + ChatWidget 处理链已打通

2) 兼容旧布尔接口（SetOrchestrationTaskState）
- 结果：通过
- 证据：legacy run_id 映射逻辑存在，且有与命名 run 并存测试

3) 空闲态 UX（状态条常显、标题回落）
- 结果：通过
- 证据：`task_complete_keeps_idle_status_visible` 用例通过

4) iTerm 绑定稳定性告警仅在 orchestration 展示路径出现
- 结果：通过
- 证据：3 个 binding warning 用例通过（附加/仅告警/清空）

5) 编译与基础回归
- 结果：通过（目标范围）
- 证据：`cargo check -p codex-tui` 通过；目标用例全部通过
- 说明：全量 `cargo test -p codex-tui` 仍有与本功能无关的 status snapshot 版本头漂移
