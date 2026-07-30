# 研究论文计划：面向工具型 AI Agent 的测试

中文 | [English](PAPER_PLAN.md) | [Agent 测试策略](AGENT_TESTING_CN.md)

状态：**会议方向待确认**。当前建议主投 ICST 2027 Research Papers。本文档会明确
区分“已有代码能够支持的结论”和“仍需实现及实验才能主张的结论”。

## 会议决策

| 会议 | 官方截止时间（AoE） | 最合适的论文定位 | 当前判断 |
|---|---:|---|---|
| [ICST 2027 Research Papers](https://conf.researchr.org/track/icst-2027/icst-2027-research-papers) | 2026-11-02 | Agent 专属变异测试与测试充分性 | 推荐 |
| [CAIN 2027 Research Track](https://conf.researchr.org/track/cain-2027/cain-2027-call-for-papers) | 2026-10-30 | Agentic Software 的可测试性架构 | 很强的替代方向 |
| [FSE 2027 Research Papers](https://conf.researchr.org/track/fse-2027/fse-2027-papers) | 2026-10-02 | 多运行时、大规模实证软件工程贡献 | 以当前进度风险较高 |
| [ICSE 2027 Research Track](https://conf.researchr.org/track/icse-2027/icse-2027-research-track) | 2026-06-30 | AI 系统的测试与分析 | 已截止 |

ICST 与 CAIN 的截止时间只差三天且审稿期重叠，同一稿件不能把其中一个作为另一个
的即时备投。ISSTA 是自然的长期目标，但 2027 CFP 尚未公开，而且 ISSTA 2026
已经出现多篇 Agent 测试和诊断论文。

## 推荐的 ICST 定位

暂定标题：

> Do Your Agent Tests Actually Detect Faults? Mutation Testing for Tool-Using
> AI Agents

核心论点：

> 任务成功评测不能衡量 Agent 测试套件发现模型、工具、Context、生命周期和策略
> 边界中真实缺陷的能力。Agent 专属变异算子、确定性 Replay 与行为充分性指标，
> 可以让这种缺陷检测能力变得可衡量。

NanoHarness 是实验 Artifact，而不是论文的主要新颖性来源。

## 新颖性边界

现有 Agent Benchmark 主要衡量任务成功率；AgentDojo 专注 Prompt Injection；
ISSTA 2026 的 AgentInspect 主要从 Agent 轨迹中诊断和分类行为故障。本文计划在
三点上与它们区分：

1. 评估对象是**测试套件**，不只是 Agent；
2. 在部署前主动植入受控的 Agent 专属缺陷；
3. 用确定性 Replay 将缺陷检测与模型采样波动、外部副作用隔离。

投稿前必须通过系统的 Related Work 调研验证该边界。目前它只是待验证假设，不能
直接当作已证明的新颖性结论。

## 计划贡献

1. 从真实 Agent 系统缺陷和组件边界中提炼变异算子分类体系。
2. 基于模型、工具、Context、生命周期、Evaluator 和策略事件的运行时无关变异
   模型。
3. 支持可复现、避免重复副作用实验的确定性 Recording/Replay 底座。
4. 以 Mutation Score 为核心、行为覆盖率为补充的 Agent 测试充分性指标。
5. 在多个 Agent 系统和场景上，对最终任务评估与完整测试流水线进行实证比较。

## 研究问题

- **RQ1——代表性：**变异算子能在多大程度上代表真实 Agent 系统缺陷？
- **RQ2——检测能力：**现有任务成功 Evaluator 和测试套件能杀死多少有效 Mutant？
- **RQ3——充分性：**Mutation Score、任务成功率与行为覆盖率之间是什么关系？
- **RQ4——复现与成本：**确定性 Replay 和副作用抑制能减少多少 Flaky 结论、模型
  调用、成本和耗时？

## Artifact 架构

```text
Scenario + Oracle
       |
MutationRunner ---- MutationOperator
       |
RecordingLLM / RecordingToolRegistry
       |
Versioned AgentTrace
       |
ReplaySession
  |             |
ReplayLLM   ReplayToolRegistry
       |
CoverageCollector + MutationReport

FaultCampaignRunner ---- 版本化 FaultPlan / FaultSession
       |
FaultInjecting Model / Tool / Context / State / Hook / Permission 边界
       |
全新的确定性 Fixture 或 Sandbox 执行
```

已有证据：

- 统一的 `RunResult`、`RunStatus` 和 `StopReason`；
- 版本化 `AgentTrace` 与 `TraceEvent`；
- 带脱敏和线程安全排序的生命周期 `TraceRecorder`；
- 模型与工具边界的严格 Recording/Replay Adapter；
- 不调用真实依赖即可复现已录制的依赖异常。
- 可序列化 Scenario、执行前验证的确定性 Oracle，以及使用全新 Engine 的场景
  执行报告。
- 包含七个初始算子、显式结果分类和正确 Mutation Score 分母的 Trace-level
  Mutation Campaign。
- 具有确定性 occurrence 匹配、全新 Engine 隔离、运行时应用证据和
  Oracle killed/survived 分类的可执行 Model、Tool、Context、State、Hook
  和 Permission Fault Campaign。
- Context Message/Snapshot、State Save/Load、Hook Stage 和 Permission Decision
  的统一记录，以及 State 与 Permission-enforcement Oracle。
- 版本化真实缺陷语料 Schema、证据与裁决约束、Derivation/留出 Validation
  隔离、一致性统计、Readiness Gate、分析 CLI 和显式未验证的 Seed Candidate。
- 显式 Universe 的行为覆盖模型，包含预声明 Target、Tool Argument 等价类、
  每个 Hit 的证据、跨运行并集、分维度结果，且不预设质量阈值。
- 不可变外部 Subject Provenance、Digest 绑定 Experiment Manifest、串行原始
  Observation，以及包含 6 个成功确定性 Observation 的真实锁定
  `langgraph==1.2.10` Adapter Pilot。
- 冻结可执行 LangGraph Tool-Fault Pilot：确定性 Oracle 杀死 Stale-result、
  Duplicate-call 和 Required-argument-drop Mutant，并分别保留底层 Attempt 和交付
  Observation 证据。

仍需完成：

- 专用 pytest fixture/marker 插件；
- 系统缺陷检索、独立人类 Coding 与语料冻结；
- 对初始 Mutation Operator 的真实缺陷验证；
- 用于控制流变异的 branch-aware Replay 或 Sandbox 实验对象；
- 在外部 Runtime 和 Recovery Scenario 上验证边界 Fault；
- 实验对象专属冻结 Coverage Model 与实证实验报告；
- 论文级 LangGraph Tool/Fault Scenario 和外部 Benchmark 来源；
- Confirmatory Manifest、原始数据和 Cluster-aware 分析脚本。

## 实验设计

### Mutation 语义

已实现的第一层修改脱离真实依赖的 Report 与统一 Trace，再重新执行确定性
Oracle。它直接衡量 Oracle 能否发现可观测数据损坏，同时排除模型采样和外部
副作用；不能将其描述为执行了所有变异后的 Agent 行为。改变工具选择、分支、
重试或环境状态的 Mutant，需要由确定性 Fixture 或 Sandbox 支撑的可执行层。
该层现已覆盖六类组件边界，但控制流分歧后尚不能自动重新接入 Baseline Replay。
实验必须分别报告 Trace-level 和 Executable 结果。

### 实验对象

最低可信实验应包含：

1. NanoHarness 内核的确定性场景；
2. 仓库中的完整 Coding Agent；
3. 至少一个独立开发的 Agent Runtime 或 Benchmark Adapter。

候选外部环境包括 tau3-bench 与 AgentDojo。若能增加 Google ADK 或 LangGraph
之类的独立运行时，会比只增加另一套场景更能增强外部有效性。

当前 `langgraph==1.2.10` Pilot 通过外部 Adapter 执行真实 Compiled StateGraph。
第二个 Pilot 增加确定性 Tool 和三个可执行 Fault，并由组合 Oracle 全部杀死。
两者仍是手工构造、小规模、无 Model 的 Integration Check，没有 Benchmark Dataset
或留出 Mutant，尚未满足论文的外部有效性门槛。

### 缺陷与 Mutant

- 从 NanoHarness 和公开 Agent Framework 的 Issue/Commit 历史收集并标注真实缺陷；
- 使用有记录的 Coding Protocol 推导 Mutation Operator；
- 每个 Operator 保留到真实缺陷的映射；
- 区分 killed、survived、invalid 和 equivalent Mutant；
- 由至少两名标注者复核分层样本并报告一致性。

### Baseline

- 只有最终任务成功评估；
- 只有确定性不变量 Oracle，不使用 Mutation；
- 只有行为覆盖率，不使用 Mutation Score；
- 不启用 Replay，每次重新执行真实模型和工具。

### 指标

- Mutation Score 和各 Operator Kill Rate；
- 任务成功率与不变量违反率；
- 行为覆盖率；
- Flaky Verdict 与复现率；
- Replay 避免的真实模型/工具调用数；
- 延迟、Token 与预估成本；
- Invalid/Equivalent Mutant 比例。

报告置信区间和效应量。统计检验需要根据配对与重复测量结构选择，不能为了获得
显著性而事先随意指定。

## 投稿门槛

实验冻结前至少需要：

- 一套由真实缺陷支持的冻结 Operator 分类；
- 跨越至少四类组件边界的六个可运行 Operator；
- 至少两个 Agent 系统和一个外部场景/Benchmark 来源；
- 完全脚本化、带随机种子并可 Replay 的实验流水线；
- 可匿名的复现包，包含原始结果和分析脚本；
- Mutation Score 能提供任务成功率之外信息的实证证据。

如果 2026 年 9 月中旬仍达不到这些门槛，更稳妥的做法是将论文改为 CAIN 的
“Agent 可测试性架构 + 初步证据”，而不是提交证据不足的 ICST 变异测试论文。

## 工作时间表

- **8 月 15 日前：**完成 Replay 底座、Scenario 和确定性 Oracle。
- **9 月 5 日前：**完成真实缺陷协议、初始语料、六个算子和 MutationRunner。
- **9 月 20 日前：**完成覆盖率、外部 Adapter 和 Pilot Experiment。
- **10 月 10 日前：**完成正式实验并冻结原始数据。
- **10 月 24 日前：**完成论文初稿和复现包。
- **10 月 25 日—11 月 1 日：**内部评审、Threat Analysis 与最终检查。

## 作者与 AI 使用记录

目标会议要求引用准确、可追踪，并要求在适用时披露研究活动中的 AI 使用情况。
需要维护 Research Log，记录 AI 如何参与代码、实验设计、数据收集、分析和论文
写作。所有结论、引用、数据和 Artifact 仍由人类作者负责。
