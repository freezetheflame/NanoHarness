<p align="center">
  <img src="assets/NanoharnessMain.png" alt="NanoHarness" width="640">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/Tests-508%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Framework-ETCSLV-purple.svg" alt="ETCSLV">
</p>

<h1 align="center">NanoHarness</h1>

<p align="center">
  <b>基于 H&nbsp;=&nbsp;(E,&nbsp;T,&nbsp;C,&nbsp;S,&nbsp;L,&nbsp;V) 的极简 Agent 框架</b>
</p>

[English](README.md) | 中文

---

## 概述

NanoHarness 是一个极简的 Python Agent 框架，实现了 [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness) 提出的六组件治理模型：

| | 组件 | 职责 |
|:---:|---|---|
| **E** | 执行循环 | 思考 → 行动 → 观察循环、终止条件、错误恢复 |
| **T** | 工具注册 | 类型化工具目录、路由、Schema 校验 |
| **C** | 上下文管理 | 上下文窗口的组装与压缩 |
| **S** | 状态存储 | 跨轮次持久化与崩溃恢复 |
| **L** | 生命周期钩子 | 横切面插桩：日志、策略、认证 |
| **V** | 评估 | 轨迹记录、循环中早停检测、独立目标验证 |

内核**只**提供这六个接口和一个编排引擎。其余一切——调用哪个 LLM、如何管理记忆、是否执行权限校验——均由应用层决定。

---

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       NanoHarness 内核                           │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │  E: NanoEngine                                          │  │
│   │                                                         │  │
│   │    ON_START ──► Think ──► Act ──► Observe ──► ON_STEP   │  │
│   │                    │         │          │       │        │  │
│   │                    ▼         ▼          ▼       ▼        │  │
│   │               LLMProtocol  T: Tools  C: Context         │  │
│   │                                              V: Eval    │  │
│   │                                    should_stop? ──► STOP │  │
│   │                                                         │  │
│   │    ON_END ◄── V: Report + evaluate_success              │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   接口：  BaseToolRegistry  BaseContextManager                  │
│           BaseStateStore    BaseHookManager                     │
│           BaseEvaluator     LLMProtocol                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                        构造函数注入
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         应用层                                  │
│                                                                 │
│   LLM 适配器  ·  记忆策略  ·  权限策略  ·  工具组装              │
│   Prompt 模板  ·  UI / 输出                                     │
│                                                                 │
│   组装：main.py 或各项目专属 builder                             │
└─────────────────────────────────────────────────────────────────┘
```

**设计原则：** 引擎不知道 Prompt、记忆、权限或 I/O 的存在。所有行为通过注入实现，使内核可安全地在不同 Agent 应用间共享。

---

## 结构

```
nanoharness/
  core/                  # 内核：接口 + 引擎
    schema.py            #   交互、评估与统一 RunResult 模型
    base.py              #   ETCSLV ABCs, LLMProtocol, HookStage
    engine.py            #   NanoEngine（含循环中评估）
    prompt.py            #   PromptManager（YAML 模板加载器）
  components/            # ETCSLV 最简实现
    tools/               #   T: DictToolRegistry, ScriptToolRegistry
    context/             #   C: SimpleContextManager
    state/               #   S: JsonStateStore
    hooks/               #   L: SimpleHookManager
    evaluator/           #   V: TraceEvaluator（含 should_stop + evaluate_success）
  testing/               # Trace 记录与后续 Replay/测试组件
  utils/                 # get_logger, count_tokens
configs/
  prompts.yaml           # Prompt 模板
  scripts/               # Shell 脚本工具（自动发现，27 个）
examples/
  coding_agent/          # 完整 Coding Agent 参考（434 个测试）
tests/                   # 内核契约测试
```

---

## 快速开始

```bash
git clone https://github.com/HabitGraylight/NanoHarness.git
cd NanoHarness
pip install -e .
```

内核仅依赖 Pydantic 和 PyYAML。LLM 客户端和其他集成由各应用按需安装。

```bash
# 运行最简示例
python main.py

# 运行 Coding Agent
cd examples/coding_agent && python main.py
```

---

## 引擎循环

```
NanoEngine.run(query)
     │
     ├─ L.trigger(ON_TASK_START)
     ├─ C.add_message(user)
     │
     └─ 循环直到终止或达到 max_steps:
          │
          ├─ Think:  E → LLM.chat(C.get_full_context(), T.get_schemas())
          ├─ L.trigger(ON_THOUGHT_READY)
          │
          ├─ Act:    对每个 tool_call:
          │            可选权限门控 → T.call(name, args)
          │            C.add_message(observation)
          │
          ├─ S.save_state()
          ├─ V.log_step()
          ├─ V.should_stop()?  ──► 若陷入循环/停滞则提前终止
          └─ L.trigger(ON_STEP_END)

     ├─ V.get_report()        （包含 evaluate_success 验证结果）
     ├─ E 构建 RunResult      （状态 + 停止原因 + 最终回答 + 轨迹）
     └─ L.trigger(ON_TASK_END)
```

引擎内部没有记忆、Prompt 渲染或权限逻辑——全部通过注入的组件和钩子流转。

`RunResult.status` 描述执行如何结束（`completed`、`stopped` 或
`exhausted`），`RunResult.evaluation.achieved` 则是唯一权威的成功结论。
因此 Agent 停止不再自动等同于完成用户目标。迁移期间仍保留
`report["summary"]` 与 `report["trajectory"]` 的旧式访问方式。

无需修改 Engine 即可采集生命周期 Trace：

```python
from nanoharness.testing import TraceRecorder

recorder = TraceRecorder()
recorder.attach(hooks)
result = engine.run("完成任务")
trace_json = recorder.snapshot().model_dump_json(indent=2)
```

Trace Payload 会被转换为带版本的 NanoHarness 模型。常见的密钥字段默认脱敏；
如果自由文本中也可能包含密钥，应用可以注入更严格的 Redactor。

模型和工具边界支持确定性录制与重放：

```python
from nanoharness.testing import (
    RecordingLLM,
    RecordingToolRegistry,
    ReplayLLM,
    ReplaySession,
    ReplayToolRegistry,
)

# 录制一次真实运行。
recording_llm = RecordingLLM(live_llm, recorder)
recording_tools = RecordingToolRegistry(live_tools, recorder)

# 不调用真实依赖，重放同一次运行。
session = ReplaySession(recorder.snapshot())
replay_llm = ReplayLLM(session)
replay_tools = ReplayToolRegistry(session)
```

模型与工具共用一个 `ReplaySession` 时会检查全局交互顺序；严格模式还会检查
消息、Schema、工具名和参数。录制时发生的依赖异常会被转换为结构化的
`RecordedExecutionError` 确定性复现。

可序列化 Scenario 能组合确定性 Oracle，无需向 Engine 添加测试策略：

```python
from nanoharness.testing import OracleKind, OracleSpec, Scenario, ScenarioRunner

scenario = Scenario(
    scenario_id="completes-once",
    query="完成任务",
    oracles=[
        OracleSpec(kind=OracleKind.GOAL_ACHIEVEMENT, parameters={"expected": True}),
        OracleSpec(kind=OracleKind.LIFECYCLE),
    ],
)
report = ScenarioRunner(engine_factory).run(scenario)
report.raise_for_failure()
```

内置 Oracle 覆盖目标完成、运行状态、停止原因、生命周期配对、工具调用约束、
任务派生的环境状态增量、恰好一次副作用账本、组件故障和预期执行异常。所有
Oracle 配置都会在创建 Engine 或调用真实依赖前完成校验。

Trace-level Mutation Campaign 可以衡量这些 Oracle 能否发现受控的可观测故障：

```python
from nanoharness.testing import (
    ContextMessageDropOperator,
    HookSkipOperator,
    MutationRunner,
)

campaign = MutationRunner(scenario_runner).run(
    scenario,
    [HookSkipOperator(), ContextMessageDropOperator()],
)
print(campaign.mutation_score)
```

Mutation Score 的分母只包含 killed 与 survived Mutant。Baseline 失败、算子不
适用、等价、无效和变异执行错误都会被显式分类，而不会悄悄当作 survived
Mutant。

对于必须影响真实控制流的故障，`FaultPlan` 可应用于真实或确定性 Fixture 的
Model、Tool、Context、State、Hook 和 Permission 边界。`FaultCampaignRunner`
会运行干净 Baseline，并为每个 Plan 创建全新
Engine，记录规则是否触发以及是否真正改了值，再使用同一组 Scenario
Oracle 分类。改写 Model/Tool Result 的 Fault 应放在 Recording 内层；抑制
Context/State/Hook/Permission 调用的 Fault 应放在 Recording 外层，避免把已跳过
操作错记为已完成。
对应 Recording 装饰器会输出统一 Context、State、Hook 和 Permission Event。
Trace Schema v2 承载这些 Event；确定性 Replay 可迁移旧 v1 Model/Tool Trace，
且不修改源对象。

真实缺陷证据使用版本化 `DefectCorpus` 管理，而不是零散笔记。
[语料协议](REAL_DEFECT_PROTOCOL_CN.md) 将 Operator Derivation 与留出 Validation
隔离，保留独立 Coder Annotation，并定义证据和冻结门槛。查看当前草案语料：

```bash
.venv/bin/python -m nanoharness.testing.defect_cli \
  research/defects/corpus.json
```

Candidate 记录不作为已验证缺陷计数。

行为覆盖率使用预先声明的 `CoverageModel`；分母由其 Target 定义，而不是由
已观测行为反推。`CoverageCollector` 支持 Tool 与 Argument 等价类、Run/Stop
结果、Lifecycle 与组件故障、Oracle 结果、可执行 Fault 和 Trace Metadata。详见
[覆盖率协议](BEHAVIORAL_COVERAGE_CN.md) 和明确未冻结的
`research/coverage/model_template.json` Template。默认不启用 Coverage 阈值。

外部 Subject 实现小型 `SubjectAdapter` 契约。冻结 `ExperimentManifest` 在执行前将
Subject Revision、完整 Scenario、Cell 顺序、Seed 和 Metadata 绑定到 Digest。
可如下复现锁定真实 LangGraph Plumbing Pilot：

```bash
uv pip install --python .venv/bin/python -e '.[research]'
.venv/bin/python research/pilots/langgraph_deterministic/run.py \
  --output /tmp/langgraph-pilot-report.json
```

详见[实验协议](EXPERIMENT_PROTOCOL_CN.md) 和 Pilot README 中的严格主张边界；
Echo Pilot 不是论文级证据。

配套的 `research/pilots/langgraph_tool_faults/` Pilot 在真实 StateGraph 中运行三个
可执行 Tool Mutant。Attempt-aware Trace 会区分重复的底层执行和交付给 Graph State
的单一 Observation。归档 Plumbing Run 杀死了三个 Mutant，但该手工样本不是论文
Mutation Score。

外部 Benchmark 接入与 Subject 执行保持分离。锁定的 AgentDojo Converter 会冻结
Package/Commit Provenance、显式 Task ID、Environment Digest、Reference Call 与
原始 Scorer Provenance：

```bash
uv pip install --python .venv/bin/python -e '.[agentdojo-research]'
.venv/bin/python research/pilots/agentdojo_offline_conversion/run.py \
  --output-dir /tmp/agentdojo-conversion
```

转换后的 Reference Call 不是规范性唯一答案，Oracle 也保持未绑定；将这些 Scenario
用于实验前请先阅读 Pilot README 的主张边界。

tau2 Converter 同样保持离线，不导入 Benchmark 包，并固定 `tasks.json`、源码版本、
任务 Fixture、Reward Basis 与 Domain 状态摘要。`Tau2SubjectAdapter` 可在隔离的
Python 3.12 环境中，将原生 half-duplex `SimulationRun` 绑定到原始确定性 DB
Evaluator、任务派生的状态增量和语义副作用账本，同时不把 Reference Actions 当成
唯一正确调用序列。可复现入口与主张边界见 `research/pilots/tau2_native_bridge/`。

配套的 `agentdojo_scorer_bridge/` Pilot 会派生显式绑定的执行 Scenario，并让原始
Utility 在 AgentDojo 原生 Pre/Post Environment 上评分。确定性 GroundTruthPipeline
结果只验证 Bridge，不是 Agent 性能结果。

---

## 工具

工具满足 `BaseToolRegistry` 接口，提供两个方法：`get_tool_schemas()` 和 `call(name, args)`。

内置两种注册器：

- **DictToolRegistry** — 通过 `@tool` 装饰器注册 Python 函数，JSON Schema 从类型提示自动推断。
- **ScriptToolRegistry** — 自动发现目录中的 `.sh` 文件，参数通过 `@param` 注释头声明，以环境变量传递。

注册器通过 `merge()` 组合。

添加新工具无需修改 Python 代码——将带有正确头部的 Shell 脚本放入 `configs/scripts/` 即可自动可用。

---

## 扩展

内核定义接口，应用提供具体行为：

**LLM** — 实现 `LLMProtocol`：
```python
def chat(self, messages, tools=None) -> LLMResponse: ...
```

**自定义组件** — 继承任意 `Base*` ABC，注入 `NanoEngine`。

完整参考见 `examples/coding_agent/`，其中组装了自定义 LLM 适配器、记忆策略、权限流水线、子 Agent 委派、技能加载和评估——全部在内核之上构建，无需修改内核。

---

## 测试

```bash
# 内核测试（74 个）
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v

# Coding Agent 测试（434 个：291 UT + 143 ST）
cd examples/coding_agent
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

**共 508 个测试。** 内核测试只需要内核依赖与 pytest。

---

## 路线图

NanoHarness 采用问题驱动、组件化的演进路线。内核负责定义稳定契约，
可选适配器负责连接开源生态，并避免第三方对象渗透到执行引擎。

- 第一阶段：正确性与组件契约
- 第二阶段：执行、会话与恢复
- 第三阶段：记忆与生态适配器
- 第四阶段：可观测性与生产就绪

参见[完整路线图](ROADMAP_CN.md)或[英文版](ROADMAP.md)。

---

## 安全

拥有工具访问权限的 Agent 可能造成实际损害。生产部署应实现权限门控、沙箱执行和 Prompt 注入防御。参见 Coding Agent 示例中的权限流水线参考实现。

---

## 致谢

本项目的理论基础来自 [Agent Harness Survey](https://github.com/Gloriaameng/Awesome-Agent-Harness)。

---

## 引用

```bibtex
@software{nanoharness2026,
  title     = {NanoHarness: A Minimal Agent Harness Based on H=(E,T,C,S,L,V)},
  author    = {Habit},
  year      = {2026},
  url       = {https://github.com/HabitGraylight/NanoHarness},
  license   = {MIT}
}
```

理论基础：

```bibtex
@article{meng2026agentharness,
  title     = {Agent Harness for Large Language Model Agents: A Survey},
  author    = {Meng, Qianyu and Wang, Yanan and Chen, Liyi and others},
  year      = {2026},
  url       = {https://www.preprints.org/manuscript/202604.0428/v2}
}
```

---

## 许可证

MIT
