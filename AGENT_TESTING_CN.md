# Agent 测试策略

中文 | [English](AGENT_TESTING.md) | [路线图](ROADMAP_CN.md)

NanoHarness 将 Agent 评测与 Agent 测试视为相关但不同的活动。评测回答 Agent
是否完成任务；测试还必须让故障可复现、检查执行过程中的不变量、衡量测试覆盖了
哪些行为，并证明测试套件能够发现可信的缺陷。

测试包位于最小运行时内核之外，通过稳定的 NanoHarness 事件和模型工作，复用与
应用及集成相同的组件边界。

## 测试模型

| 能力 | 回答的问题 |
|---|---|
| `Evaluator` | Agent 是否完成了用户目标？ |
| `TestOracle` | 运行是否违反状态、策略、安全或生命周期不变量？ |
| `TraceRecorder` | 输入、决策、动作和状态变化具体是什么？ |
| `ReplayProvider` | 能否不调用真实模型、不重复副作用地复现故障？ |
| `CoverageCollector` | 覆盖了哪些行为、规则、状态迁移与故障？ |
| `FaultInjector` | 依赖变慢、不可用或返回畸形结果时会怎样？ |
| `MutationRunner` | 测试套件能否发现主动植入的真实缺陷？ |
| `DifferentialRunner` | 模型、Prompt、策略或运行时版本之间有什么行为差异？ |
| `FailureReducer` | 仍能复现故障的最小场景和轨迹是什么？ |

## 建议包结构

```text
nanoharness/testing/
  scenario.py       # Scenario、fixture、预期不变量与随机种子
  oracle.py         # 确定性、基于模型与 LLM 辅助的 Oracle
  trace.py          # 统一的版本化事件与 Trace 序列化
  replay.py         # 重放模型、工具、时间、重试与策略决策
  faults.py         # 声明式故障计划与注入 Hook
  coverage.py       # 行为覆盖率收集与报告
  mutation.py       # Agent 专属变异算子与 Mutation Score
  differential.py   # 受控 A/B 执行与 Trace 对比
  shrink.py         # 失败场景和轨迹缩减
  pytest_plugin.py  # fixture、断言、marker 与 CI 输出
  adapters/
    harbor.py
    inspect.py
    tau3.py
    agentdojo.py
```

## 统一 Trace 与重放

Trace 需要记录足以在不调用真实模型、不重复外部副作用的前提下复现控制流的信息：

- 发送给模型的消息和统一后的原始响应；
- 工具 Schema、调用、call ID、结果、错误和耗时；
- 权限与策略决策；
- Context 压缩和状态迁移；
- Hook 输入、输出和失败；
- Evaluator 输入与结论；
- 时间、重试、随机种子、预算和停止原因决策。

Trace Schema 必须版本化，并在持久化前脱敏。Replay 必须声明事件是模拟结果、
经过真实依赖复核，还是不支持重放。具有外部副作用的调用绝不能被隐式重复执行。

## 测试 Oracle

任务成功只是其中一种 Oracle。场景还可以定义以下不变量：

- 每次工具调用恰好对应一条终态执行记录；
- 被拒绝的调用不会到达底层工具；
- 只读任务不产生写入或外部副作用；
- 生命周期开始和结束事件成对出现；
- 完成的运行具有评估结论和明确停止原因；
- checkpoint 恢复不会重复已完成的 call ID；
- 不可信工具输出未经审批不能流入敏感操作。

优先使用确定性 Oracle。LLM 辅助 Oracle 必须公开 Prompt、模型、重复次数、
置信度和分歧情况，不能伪装成确定性断言。

## 行为覆盖率

Agent 测试充分性不能只用源码行覆盖率表示。第一版覆盖率模型包括：

- 工具与工具参数等价类覆盖率；
- Run Status 与 Stop Reason 覆盖率；
- Session 和环境状态迁移覆盖率；
- 权限、策略规则和生命周期事件覆盖率；
- 故障注入与恢复路径覆盖率；
- checkpoint 与 replay 路径覆盖率；
- 不可信 Source 到敏感 Sink 的安全覆盖率。

覆盖率报告用于指出未执行的行为，不代表已覆盖行为一定正确。在指标经过实验验证
之前，覆盖率阈值保持可选。

## 变异测试

变异引擎在组件边界植入可信缺陷，并衡量测试套件能否发现它们。第一批算子来自
NanoHarness 已经遇到的真实故障：

```text
MODEL_RESPONSE_DROP       丢失或截断模型回复
TOOL_NAME_SWAP            调用另一个已注册工具
TOOL_ARGUMENT_DROP        删除必填参数
TOOL_RESULT_STALE         返回过期 observation
TOOL_CALL_DUPLICATE       重复执行具有副作用的调用
CONTEXT_MESSAGE_DROP      丢失原始用户目标
HOOK_SKIP                 跳过生命周期事件
PERMISSION_BYPASS         被拒绝后仍然执行
CHECKPOINT_CORRUPT        修改序列化 Session 状态
EVALUATOR_FLIP            翻转 achieved 结论
TERMINATED_AS_SUCCESS     将模型停止误认为任务完成
```

核心指标是 Mutation Score：被测试发现的有效变异数除以全部有效变异数；等价和
无效变异单独报告。

## 实施顺序

1. **已实现：**定义版本化 `TraceEvent` 模型和 `TraceRecorder`。
2. **已实现：**增加模型与工具边界的确定性 Recording/Replay Adapter，并严格
   检查请求内容和跨组件调用顺序。
3. **核心已实现：**定义可序列化 `Scenario`、确定性 `TestOracle` 和
   `ScenarioRunner` 契约；专用 pytest fixture 与 marker 插件仍待实现。
4. 为模型、工具、状态和 Hook 边界增加声明式故障注入。
5. 发布第一版行为覆盖率报告。
6. 实现首批变异算子和 Mutation Score。
7. 增加差分执行与自动失败样例缩减。
8. 通过 Adapter 接入外部环境和 Scorer。

每一步都需要自己的契约测试。除非缺失的运行时事件或模型本身就是需要引入的
契约，否则测试组件不应要求修改 `NanoEngine`。

## 生态适配器

- [Harbor](https://github.com/harbor-framework/harbor)：容器化、并行 Benchmark
  环境和 Agent Adapter。
- [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai)：Dataset、Scorer、
  Sandbox 与评测组合。
- [tau3-bench](https://github.com/sierra-research/tau2-bench)：有状态、受业务策略
  约束的工具—Agent—用户场景。
- [AgentDojo](https://github.com/ethz-spylab/agentdojo)：Prompt Injection 攻击、
  防御与安全场景。
- [Hypothesis](https://github.com/HypothesisWorks/hypothesis)：工具参数与动作序列
  的生成和 Shrinking。

Adapter 负责把外部对象转换为 NanoHarness Scenario、Trace 和 Verdict；外部
Benchmark 格式不能成为内核接口。

## 研究与文章计划

核心待验证论点是：仅使用任务成功评测不足以支撑 Agent 可靠性工程。实验回答：

1. 确定性 Replay 能否提高故障复现率并降低诊断成本？
2. 行为覆盖率能否发现聚合任务成功率掩盖的测试缺口？
3. Mutation Score 能否衡量 Agent 测试的缺陷检测能力？
4. 故障注入能否发现正常 Benchmark 不会暴露的恢复和生命周期问题？

实验先使用确定性的 NanoHarness 场景与 Coding Agent 历史缺陷，再扩展到
tau3-bench 或 AgentDojo。报告任务成功率、不变量违反率、Mutation Score、
恢复率、Flaky Rate、复现率、诊断耗时、延迟与 Token 成本；比较只有最终评估的
基线与完整测试流水线。

暂定文章主题为：**从 Agent 评测到 Agent 测试：将软件测试原则引入 Agent
Harness**。
