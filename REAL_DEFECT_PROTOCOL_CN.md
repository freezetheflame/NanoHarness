# Agent 测试真实缺陷语料协议

中文 | [English](REAL_DEFECT_PROTOCOL.md) | [论文计划](PAPER_PLAN_CN.md)

状态：**协议草案，会议方向待确认**。协议版本：`1.0-draft`。开始系统
收集前，必须预注册门槛、仓库、时间窗口、检索式和分区规则。

## 目的与主张边界

该语料用于 RQ1：Agent 专属变异算子能否代表真实 Agent 系统中观察到的
缺陷。`candidate` 记录只是待审查输入，不是实证证据。只有 `verified`
记录可以进入缺陷计数、算子支持主张和留出集代表性结果。

分析单位是一个根因对应的一个实现缺陷。同一根因的多份报告合并并保留
`duplicate_of` 链接；同一 PR 修复的多个不同根因则保持为多条记录。

## 来源抽样

检索前冻结一份 Sampling Manifest，包含：

- 仓库名与不可变仓库 Revision；
- 收集起止日期和缺陷修复时间窗口；
- 完整 Issue Label、检索式、API Query 和分页限制；
- 是否纳入未修复的已关闭 Issue 和只有 Commit 的缺陷；
- 去重和语料分区规则。

来源包括 NanoHarness 和独立开发的 Agent Runtime。Framework 必须根据公开历史、
Tool-use 能力、活跃度和测试可用性等显式标准选择，不能因为它们的 Issue
刚好匹配某个算子而选择。筛选前保存原始检索输出，使 Candidate Flow 和排除
数量可重建。

## 纳入与排除

同时满足以下条件时纳入：

1. 描述已实现行为不正确，而不是功能请求；
2. 影响 Agent Runtime、Harness、Tool 边界、Context/State、Policy/Permission、
   Lifecycle、Evaluator 或 Replay；
3. 可以说明触发条件和外部可观测症状；
4. 稳定证据能定位报告、复现、测试或修复变更；
5. 位于预注册仓库和时间窗口内。

纯文档问题、不支持的用法咨询、纯上游故障、无确认证据且无法复现、超出范围
或重复的条目应排除并保留原因。安全缺陷如果来自 Agent 执行或 Policy 边界，
仍然符合条件，不能仅因为它是安全问题而排除。

## 证据标准

一条已验证缺陷必须包含：

- 稳定的仓库和缺陷 ID；
- 简明的症状、触发条件、根因和影响；
- 至少一个受影响组件边界；
- 至少两种不同证据角色；
- 至少一份修复变更、回归测试或复现；
- 最终纳入理由和 derivation/validation 分区。

每份证据记录 URL 或本地 Locator、可用时的不可变 Revision、文件/测试 ID、
可变页面的访问日期，以及许可证允许时的归档 Hash。单独的 Fix Commit 不能证明
某份 Issue 报告了同一失败，Coder 必须检查链接 Artifact。

## 防止数据泄漏的分区

Derivation 分区用于开发并冻结算子分类。Validation 分区在算子名称、前置条件和
语义冻结前不可见；预注册 RQ1 分析前，不能用 Validation 缺陷新增或修改算子。

所有 Candidate 使用同一预注册规则。优先时间切分：较早的已解决缺陷用于
Derivation，较新缺陷用于 Validation，切分日期在 Coding 前确定。如历史过少，
使用按仓库分层的确定性 Hash 切分，并公开代码和 Seed。不得手工将难以匹配的
Validation 缺陷移入 Derivation。

RQ1 报告已验证 Validation 缺陷中至少匹配一个冻结算子的比例、各边界匹配率、
需要算子组合的缺陷和未匹配根因类别。Derivation 结果只是描述性的，不能证明外部
代表性。

## 独立 Coding 与裁决

可行时由两名人类 Coder 独立编码每个 Candidate。AI 可以帮助检索或摘要来源，
但不计作独立人类 Coder，并必须披露其作用。

Coding 分两轮：

1. **Pass A，对算子盲法：**在不查看候选算子映射的情况下判定
   include/exclude/uncertain，并标注症状、根因、触发条件、影响和组件边界。
2. **Pass B，冻结分类：**将已纳入缺陷映射到零个或多个算子，并记录前置条件和语义
   为何匹配。

保留两份原始 Annotation。通过有记录的共识或第三名人类 Adjudicator 解决分歧，
不得覆盖独立记录。裁决前报告原始一致性：纳入决策使用 Cohen's kappa，Boundary 和
Operator 多标签使用平均 Jaccard。同时报告双人 Coding 样本数和类别流行率，因为
kappa 受流行率影响。

## 冻结与审计门槛

设置 `frozen_at` 前，所有 Candidate 必须变为 `verified` 或 `excluded`，并通过预注册
最低门槛。`DefectCorpusAnalyzer.assess_readiness` 检查：

- 最少已验证数和留出 Validation 数；
- 最少双人 Coding 数；
- 未解决 Candidate 或 Dispute；
- 没有 Verified Derivation 缺陷动机的冻结算子。

机器可读语料位于 `research/defects/corpus.json`。当前 Seed 记录特意标记为
`candidate`；它只演示 Pipeline，在人类 Coding 和裁决完成前不得进入论文结果。

## 可复现输出

复现包必须包含 Sampling Manifest、许可再分发时的原始检索数据、筛选决策与排除
原因、独立 Annotation、裁决日志、冻结 Corpus JSON、Operator Catalog、分析命令和生成的
汇总表。公开报告可能需要脱敏，但稳定 ID 必须允许授权审计者将记录与保存来源
重新关联。
