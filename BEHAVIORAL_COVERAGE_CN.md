# Agent 测试行为覆盖率协议

中文 | [English](BEHAVIORAL_COVERAGE.md) | [论文计划](PAPER_PLAN_CN.md)

状态：**初始实现已完成，实证验证待完成**。该协议定义 RQ3 的第一版指标，
不主张“已覆盖的行为就一定正确”，也不主张更高百分比始终代表更强测试套件。

## 显式分母

每个实验对象在开始被测执行前，都必须具有版本化 `CoverageModel`，其中列出稳定
`CoverageTarget` ID。所有启用 Target 的数量是分母。Collector 绝不从已观测
Trace 中反向创建 Target：否则只包含已执行行为的测试套件会天然显示 100%
覆盖，也无法进行有意义的比较。

Target 应从实验对象公开 Tool Schema、Runtime State Machine、Policy Rule、
Fault Catalog 和测试需求中推导。更改分母必须创建新模型版本。复现包应公开
模型和推导过程；草案 Template 不是已冻结的实验分母。

已冻结模型会绑定实验对象/Revision、推导说明、带时区的冻结时间，以及有序 Target
Universe 的 SHA-256 Digest。实验代码使用 `require_frozen=True`；这会拒绝
Template、Draft 和冻结后被修改的 Target。

## 已实现维度

第一版 Collector 支持当前稳定契约真正能提供证据的维度：

- Tool Call；
- Tool Argument 等价类（`present`、`missing`、`equals`、`one_of`、JSON
  `type`、数值 `range` 和 `regex`）；
- Context Message Role、State Save/Load Value Class、Hook Stage Outcome 和
  Permission Allow/Deny Decision；
- `RunStatus` 和 `StopReason`；
- Lifecycle Event Type；
- Model、Tool 和 Hook Error Event；
- 统一 Execution Error；
- 确定性 Oracle 通过/失败结果；
- 已注入 Fault Action 以及对应 Scenario 是否通过；
- 已声明 Trace Metadata，例如 Baseline/Fault-injected 模式。

State Machine 的完整 Transition、Checkpoint Restore/Recovery Path、Retry/Time Event
和 Untrusted-source-to-sensitive-sink Flow 仍未覆盖，因为 NanoHarness 尚未对它们全部
提供稳定统一 Event。在这些维度进入分母前，必须先增加 Runtime Contract。没有
Target 不代表该行为已被测试。

## 证据与聚合

每个 Hit 保留 Scenario ID、Trace ID、Source，以及存在 Event 时的 Event ID/Sequence。
证据细节会统一化，并使用默认敏感字段脱敏。重复 Hit 会增加 `hit_count`，但不会
增加已覆盖 Target 数。

聚合是同一不可变 Model 上的集合并集。应报告：

- Target 总体的 covered/total 和 Ratio；
- 每个维度的 covered/total 和 Ratio；
- 未覆盖 Target ID 和未覆盖 Required Target ID；
- Hit Count 与支持它的执行证据。

不得把不兼容实验对象模型的 Target 数量合并成一个 Micro Ratio，否则大模型会占据主导。
应分别报告每个实验对象和维度；如果跨对象汇总合理，同时报告 Macro Average、
不确定性和每个独立值。

## Fault 与 Recovery 解释

`fault_action` Hit 只证明某条规则已应用，不证明 Agent 观察到或从 Fault 中恢复。
`fault_outcome` 将已应用规则与 `ScenarioReport.passed` 组合。只有 Scenario 包含
直接验证指定恢复不变式的确定性 Oracle 时，才能称为 Recovery Coverage。Warning-only
失败不会使 Scenario 失败，必须单独分析。

## 门槛与 RQ3

Coverage 没有默认通过阈值。`CoverageReport.evaluate_gate` 只强制用户显式选择的
Required Target 和 Ratio。在实证证据建立它与真实缺陷检测的关系前，论文不得推荐阈值。

RQ3 在配对测试套件/Scenario 上比较任务成功、行为覆盖率和 Mutation Score。留出
分析前冻结 Coverage Model 和 Mutation Operator。报告带置信区间的相关性，并检查
不一致样例，例如高 Coverage 仍有 Surviving Mutant，或任务成功但 Coverage 很低。
不得将同一 Scenario 的重复执行或多个 Mutant 当作统计上独立的观测。
