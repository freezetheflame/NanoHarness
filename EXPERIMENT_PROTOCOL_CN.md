# Agent 测试实验与复现协议

中文 | [English](EXPERIMENT_PROTOCOL.md) | [论文计划](PAPER_PLAN_CN.md)

状态：**草案，会议方向待确认**。该协议定义执行和数据完整性规则；Hypothesis、
最终 Subject、Sample-size Rationale 和分析门槛必须在 Confirmatory Run 前冻结。

## 冻结执行单元

`ExperimentManifest` 将以下内容绑定到 SHA-256 Digest：

- 不可变 Subject Identity、Runtime Version、Revision 和 Source；
- 完整可序列化 Scenario 和确定性 Oracle；
- Cell 顺序和每个 Repetition Seed；
- Experiment ID 和预注册 Metadata。

Runner 会拒绝被修改的 Manifest、缺失/多余 Adapter、Identity Drift、重复 Trace ID、
Scenario/Seed Provenance 不匹配、无时区或倒退的 Wall Time，以及倒退的单调时钟。
它按 Manifest 顺序串行执行 Cell。只有在具备显式确定性 Scheduler 和隔离环境时才增加并行。

Seed 是执行 Metadata，不是确定性证明。允许重复相同 Seed 来测量剩余非确定性；
不同 Seed 用于测量已配置变化。每个 Adapter 必须说明 Seed 真正控制了哪些随机源。

## Subject 与 Adapter 要求

除非 Manifest 明确研究持久状态，每个 Observation 都使用全新 Runtime/Graph/Engine。
独立开发 Subject 必须提供稳定 Source URL 和不可变 Package/Commit Revision。Adapter
统一 Lifecycle 和 Subject Provenance，但不声称不同 Runtime 的 State 在语义上相同。

使用 Fake 的外部 Adapter Contract Test 只能证明接口行为。至少一个锁定版本的真实依赖
必须在 CI 或单独记录的 Integration Job 中执行。当前 LangGraph Pilot 满足 Plumbing Check，
但刻意保持小规模，不能当作论文级外部验证。

可执行外部 Fault Campaign 使用独立 Digest-bound Manifest，其中包含完整 Fault Plan。
Baseline 和每个 Plan 都创建全新 Adapter/Graph。对 Tool 而言，底层 Attempt Event 与
最终交付 Observation 分离，避免 Argument Mutation、Duplicate Execution 或 Stale Result
因装饰器顺序而被隐藏。

## 实验条件

对每个符合条件的 Subject/Scenario Pair，在支持时保留以下原始结果：

1. 只使用最终 Task-success Evaluation；
2. 不使用 Mutation 的确定性 Invariant Oracle；
3. 不发起新外部调用的 Trace-level Mutation；
4. 在全新确定性 Fixture 或 Sandbox 上执行 Boundary Fault；
5. 不使用 Replay 的 Live Execution；
6. 对同一已记录 Boundary Interaction 进行 Deterministic Replay。

各条件使用同一冻结 Scenario 和相关 Mutant Identity 配对。不支持的条件记为显式
Missing Cell，不记为 0。Baseline Failure、Invalid、Equivalent、Not-applicable 和
Infrastructure Error 不进入 killed/survived 分母，但必须报告。

## 原始 Observation 与 Artifact

不得覆盖已归档 Confirmatory Run。保存：

- 冻结 Manifest 和 Digest；
- 完整 Scenario、Trace、RunResult、Verdict 和 Fault/Mutation Evidence；
- Subject 与 Harness Revision；
- Repetition Index 和 Seed；
- 可用时的 Latency、Model/Tool Call、Token/Cost Input；
- Environment 与 Dependency Lock 信息；
- Command、stdout/stderr、Failure Status 和 Artifact SHA-256。

Timestamp、Duration 和 UUID 等 Runtime Noise 意味着重跑不必字节相同。复现应比较预注册语义
字段，并将其一致性与 Timing Variation 分开报告。公开序列化前完成脱敏；私有原始映射
需要访问控制。

## 主要 Estimand

- **RQ1：**留出 Validation Defect 映射到冻结 Operator Taxonomy 的比例，整体和各组件边界分别报告。
- **RQ2：**只有最终 Evaluator 与完整确定性 Oracle Suite 的 Mutation Score 和各 Operator Kill Rate。
- **RQ3：**Task Success、冻结 Behavioral Coverage 和 Mutation Score 的关联与不一致。
- **RQ4：**Live Rerun 与 Replay 在 Verdict 复现、Live Call、Latency、Token 使用和估算成本上的配对差值。

当多个重复 Run 或 Mutant 属于同一 Scenario 时，它们不是独立推断单元。必须保留
Subject/Scenario/Operator Cluster，使用 Paired 或 Cluster-aware Interval。报告 Effect Size
和 Confidence Interval。Null-hypothesis Test 应由预注册数据结构决定，不得在观察到
哪个 Test 显著后再选择。同时报告 Missingness 和 Failure。

## Pilot 与 Confirmatory Evidence

Pilot 可以调试 Adapter、估计 Runtime/Cost/Variance 并改进操作流程。Pilot Observation 不得
进入 Confirmatory Estimate，也不得在没有新冻结版本时用来事后修改 Target/Operator。
所有变更都保留在 Research Log。

Confirmatory Execution 前冻结：

- Venue Framing、RQ、Hypothesis、Subject 和 Exclusion Criteria；
- 真实缺陷语料和 Operator Taxonomy；
- Subject-specific Coverage Model；
- Scenario Cell、Seed/Repetition、Condition 和 Sample-size Rationale；
- Primary Estimand、Aggregation Unit、Missing-data Handling 和 Stop Rule。
