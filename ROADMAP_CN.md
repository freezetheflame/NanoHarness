# NanoHarness 路线图

中文 | [English](ROADMAP.md)

NanoHarness 以问题驱动的组件演进，而不是简单堆积功能。内核保持精简并定义
稳定契约；集成层负责把这些契约适配到外部开源项目。

## 设计原则

1. **先明确问题，再增加组件。** 每个组件都必须说明它处理的故障模式或运维需求。
2. **先定义端口，再提供适配器。** 核心协议与厂商无关；Mem0、LiteLLM、Redis
   等项目通过可选适配器接入。
3. **使用统一的内部模型。** 第三方响应对象必须在边界转换为 NanoHarness 模型。
4. **策略与机制分离。** 例如，记忆 Provider 负责存储和检索，记忆 Policy 决定
   召回什么以及保留什么。
5. **依赖保持可选。** 集成不能扩大内核的必选依赖范围。
6. **用契约测试保障扩展性。** 官方与社区适配器应运行同一套一致性测试。

## 组件地图

| 问题 | 组件契约 | 预期结果 | 候选适配器 |
|---|---|---|---|
| Agent 停止被误认为任务成功 | `EvaluationResult` / `Evaluator` | 唯一权威的成功结论 | DeepEval、Ragas、自定义评估器 |
| 任务身份和元数据在组件间丢失 | `TaskContext` | 稳定传递 query、run ID 和元数据 | 应用自定义元数据 |
| 并行工具调用从轨迹中丢失 | `ToolExecution` | 每次工具调用都有可审计记录 | MCP、本地工具 |
| 权限、Hook、重试和执行混入 Engine | `ToolExecutor` | 可组合的工具执行流水线 | MCP、沙箱和远程执行器 |
| 复用 Engine 污染新任务 | `AgentSession` | 显式任务隔离和继续执行 | 内存、Redis、数据库会话 |
| 进程崩溃后无法恢复 | `CheckpointStore` | 可恢复上下文、轨迹和步骤状态 | JSON、SQLite、Redis、PostgreSQL |
| 长期记忆与单一实现耦合 | `MemoryProvider` + `MemoryPolicy` | 可替换的存储与召回策略 | Mem0、Qdrant、Chroma、自定义存储 |
| 模型 API 需要重复编写胶水代码 | `LLMProvider` | 统一模型响应和能力描述 | LiteLLM、OpenAI、Anthropic |
| 生产任务难以检查和分析 | `TelemetryProvider` | Trace、指标、Token 和成本 | OpenTelemetry、LangFuse |

## 第一阶段——正确性与契约

目标：先保证同步单 Agent 内核语义可靠，再扩大功能范围。

- 以目标评估作为权威成功结果，并区分 `terminated`、`achieved`、`failed`
  和 `cancelled`。
- 定义统一的 `RunResult`、`RunStatus` 和 `StopReason`，明确区分步数耗尽、
  预算耗尽、错误、评估器停止和用户取消。
- 引入 `TaskContext`，承载 query、run ID、元数据和任务级信号。
- 定义与厂商无关的 `ToolSpec`，正确生成嵌套类型 Schema，并在执行前强制
  校验参数。
- 引入 `ToolExecution`，让每次工具调用都有独立结果、错误和耗时信息。
- 即使模型、状态、评估器或 Hook 失败，也保证生命周期事件成对执行和组件清理。
- 定义新任务与继续任务的显式语义，防止上下文和轨迹跨任务泄漏。
- 发布 Evaluator、Context、State 和 Tool 组件的契约测试。

## 第二阶段——执行、会话与恢复

目标：让 `NanoEngine` 专注编排，同时保持执行和持久化能力可替换。

- 增加 `ToolExecutor` 流水线，负责校验、权限、前后置 Hook、超时、重试、
  取消和结果标准化。
- 引入 `AgentSession` 作为可序列化的运行状态。
- 将状态存储升级为 checkpoint，恢复上下文、轨迹、当前步骤和组件状态。
- 增加带版本的 checkpoint Schema，并规定迁移行为。
- 提供 JSON、SQLite checkpoint 参考实现，随后增加 Redis 适配器。
- 引入步骤、时间、Token、成本、工具调用和子 Agent 并发预算。
- 对工具副作用分类，并通过 call ID、幂等键和恢复期重放保护避免重复执行。
- 定义共享组件和 Session 级组件的资源归属、清理及并发保证。
- 增加异步执行和流式输出，同时尽量保持同步组件契约不变。

## 第三阶段——记忆与生态适配器

目标：允许接入外部能力，同时避免内核绑定任何单一项目。

- 定义 `Memory`、`MemoryProvider` 和 `MemoryPolicy` 契约。
- 提供内存参考实现和适配器契约测试。
- 将 Mem0 作为第一个长期记忆官方适配器。
- 增加 LiteLLM 模型路由，并统一 Provider 能力发现。
- 将 MCP 作为工具适配器，把 Schema 和结果转换为 NanoHarness 模型。
- 提供 `nanoharness[mem0]`、`nanoharness[litellm]`、
  `nanoharness[redis]` 等可选依赖。
- 发布核心协议、序列化模型和适配器的兼容性、语义化版本及弃用规则。

记忆边界会明确区分机制与策略：

```text
MemoryProvider：add、search、update、delete
MemoryPolicy：  recall、rank、inject、consolidate
```

Mem0 可以实现 Provider 契约，同时应用仍然可以替换召回和保留策略。

## 第四阶段——可观测性与生产就绪

目标：让运行过程在规模化场景中可检查、可衡量并可安全运维。

- 定义 run、step、model 和 tool span 的遥测契约。
- 增加 OpenTelemetry 和 LangFuse 适配器。
- 报告 Token、耗时、重试、工具失败和预估成本。
- 在 Session 与 checkpoint 语义稳定后增加分布式和多 Agent 编排。
- 定义明确的沙箱、密钥、网络、权限和审计契约，而不仅依赖应用约定。
- 增加确定性 Trace Replay，以及针对模型、工具、状态和 Hook 失败的故障
  注入测试。
- 构建自动化 ETCSLV 完备度矩阵。
- 发布权限策略、沙箱、密钥和 Prompt Injection 边界的生产指南。

## 首个生态里程碑：记忆

第一个端到端集成将用于验证适配器设计：

1. 定义与厂商无关的 `Memory` 和 `MemoryProvider` 模型。
2. 将召回与保留决策拆分到 `MemoryPolicy`。
3. 提供用于确定性测试的内存实现。
4. 通过可选依赖实现 Mem0 适配器。
5. 对两种实现运行相同的 Provider 契约测试。
6. 通过生命周期 Hook 演示记忆召回和整合，不修改 `NanoEngine`。

## 适配器验收标准

官方适配器需要满足：

- 不通过核心接口暴露第三方专属对象；
- 显式声明能力和不支持的操作；
- 保持依赖可选；
- 将 Provider 错误转换为统一的 NanoHarness 错误；
- 通过共享契约测试；
- 包含最小示例和安全说明；
- 记录经过测试的上游版本。
