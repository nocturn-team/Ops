# DevOps Agent — 你的自动化DevOps Agent，控制一切

> 内循环让 LLM 在 Sandbox 里用 TDD + AI Review 撞墙证明正确，外循环让代码用 A/B + SLO + Auto-Rollback 守护线上可用。只有内循环收敛了，外循环才开门；外循环一旦报警，直接踹门。

---

## 快速开始

### 安装依赖

```bash
# 需要 Python >= 3.12
uv sync
```

### 最简用法（3 行启动）

```python
from libs.devops import Orchestrator

orch = Orchestrator(
    api_key="sk-...",
    base_url="https://api.deepseek.com/v1",   # 任意 OpenAI 兼容 API
    model="deepseek-v4-flash",
)

ctx = orch.run(
    logs="ERROR 2024-01-15 TypeError: unsupported operand ...",
    source_code="def calculate_refund(order): ...",
)

print(ctx.run.state)  # completed / human_escalation / rolled_back
```

### 命令行运行示例

```bash
# 环境变量
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
export OPENAI_MODEL="deepseek-v4-flash"
python main.py

# 或命令行参数
python main.py --api-key sk-... --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash

# 混合使用（命令行覆盖环境变量）
export OPENAI_API_KEY="sk-..."
python main.py --model gpt-4o-mini
```

### 环境变量一览

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | LLM API 密钥（必填） |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 地址，支持任意 OpenAI 兼容服务 |
| `OPENAI_MODEL` | `gpt-4o` | 模型名称 |
| `PROJECT_ROOT` | `.` | 沙箱项目根目录 |
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus 地址（外循环） |
| `CANARY_WINDOW_SEC` | `5` | 金丝雀观察窗口（秒） |

---

## 架构总览

```
输入(日志/代码/告警)
    │
    ▼
╔══════════════════════════════════════════════════╗
║              Orchestrator (状态机)                ║
║          确定性调度 · 持久化状态 · 熔断决策         ║
╚══════════════════╤═══════════════════════════════╝
                   │
    ┌──────────────┼──────────────────┐
    ▼              ▼                  ▼
Bug-Analyzer  Bug-Recurrence    Test-Driver
  (LLM)        (LLM+沙箱)         (LLM)
    │              │                  │
    └──────────────┴──────────────────┘
                   │
                   ▼
            ┌─────────────┐      ┌─────────────┐
            │   Planner   │◄────►│ Plan-Review  │
            │   (LLM)     │      │   (LLM)      │
            └──────┬──────┘      └──────────────┘
                   │
    ╔══════════════╧══════════════════════╗
    ║      内循环 (Correctness Loop)       ║
    ║  TDD-Executor → Code-Reviewer       ║
    ║       → Sandbox Runner              ║
    ║  最多 3 轮，出不去就熔断升人工        ║
    ╚══════════════╤══════════════════════╝
                   │ [全部 PASS]
                   ▼
            PR-Generator → CI Build
                   │
    ╔══════════════╧══════════════════════╗
    ║      外循环 (Availability Loop)      ║
    ║  5% → 25% → 50% → 100%             ║
    ║  纯代码，LLM 不碰闸刀               ║
    ║  指标异常 → 自动回滚                 ║
    ╚══════════════╤══════════════════════╝
                   │
                   ▼
            [完成] / [回滚] / [人工接管]
```

---

## 项目结构

```
libs/
├── agent/                          # 底层 LLM Agent 框架
│   ├── agent.py                    # Agent + Session 核心类
│   ├── _provider.py                # ProviderAdapter 协议 + OpenAI 适配器
│   ├── _streaming.py               # SSE 流式响应迭代器
│   ├── _tools.py                   # 工具注册 + 自省 + JSON Schema 生成
│   └── _types.py                   # Message / ToolCall / Event 数据类型
│
└── devops/                         # 双循环 DevOps 管线
    ├── types.py                    # 20+ 数据类型定义
    ├── context.py                  # SharedContext 跨 Agent 共享上下文
    ├── circuit_breaker.py          # 熔断矩阵（7 个节点）
    ├── orchestrator.py             # 状态机编排器（9 个阶段）
    │
    ├── agents/                     # LLM Agent（内循环）
    │   ├── bug_analyzer.py         # 读日志/代码 → BugReport
    │   ├── bug_recurrence.py       # 写复现脚本 → 沙箱验证
    │   ├── test_driver.py          # 写 Red Test（正确性边界）
    │   ├── planner.py              # 制定 FixPlan（限定范围）
    │   ├── plan_review.py          # 对抗审查（闸门）
    │   ├── tdd_executor.py         # TDD 写 Patch（内循环本体）
    │   └── code_reviewer.py        # AI Review（抓幻觉）
    │
    ├── runners/                    # 确定性代码组件
    │   ├── sandbox.py              # 编译 → pytest → ruff → 变异测试
    │   └── pr_generator.py         # Git 分支 → 提交 → 推送 → PR
    │
    └── deploy/                     # 外循环（纯代码）
        ├── release.py              # 金丝雀发布控制器
        └── sre_guard.py            # Prometheus + SLO 判定 + 自动回滚
```

---

## 核心概念

### 两个循环

| | 内循环 (Correctness) | 外循环 (Availability) |
|---|---|---|
| **目标** | Patch 被测试证明正确 | 线上服务不炸 |
| **速度** | 可以慢（Sandbox 里随便折腾） | 必须快（秒级止损） |
| **角色** | LLM 反复试错 + AI Review 守门 | 代码硬控，LLM 不碰闸刀 |
| **失败策略** | 撞墙 N 次后升人工 | 自动回滚，人工事后 debug |
| **连接点** | Sandbox 全部通过 → PR → CI Build | 镜像就绪 → 金丝雀 → 监控 |

### 管线 9 阶段

| # | 阶段 | 类型 | 说明 |
|---|---|---|---|
| 1 | Bug Analysis | LLM | 读日志/代码，输出结构化 BugReport |
| 2 | Bug Recurrence | LLM + 沙箱 | 写复现脚本并执行验证 |
| 3 | Test Driving | LLM | 写 Red Test，定义正确性边界 |
| 4 | Planning | LLM | 制定 FixPlan（strategy / files / max_lines / risk） |
| 5 | Plan Review | LLM | 对抗验证，不合格不给进内循环 |
| 6 | Inner Loop | LLM + 代码 | TDD-Executor → Code-Reviewer → Sandbox，最多 3 轮 |
| 7 | PR Generation | 代码 | `fix/{trace-id}` 分支 + commit + PR |
| 8 | CI Build | 代码 | 构建 immutable image + OPA 检查 |
| 9 | Outer Loop | 代码 | 金丝雀 5%→25%→50%→100% + SLO 监控 |

### 熔断矩阵

| 节点 | 熔断条件 | 触发方 |
|---|---|---|
| Bug-Recurrence | 复现失败 ≥ 3 次 | Orchestrator |
| Plan-Review | 拒绝 ≥ 2 次 | Orchestrator |
| Code-Reviewer | 拒绝 ≥ 2 次 | Orchestrator |
| Sandbox-Runner | 测试/Lint/变异 挂 ≥ 2 次 | Orchestrator |
| Canary 5% | 错误率 > 0.1% 或 P99 超阈值 | SRE-Guard |
| Canary 25% | 核心业务指标异常 | SRE-Guard |
| 高风险变更 | 涉及支付/鉴权/DB Schema | OPA Gatekeeper |

---

## 使用方式

### 方式一：简单模式（推荐）

传入 `api_key` / `base_url` / `model`，所有 Agent 自动创建：

```python
from libs.devops import Orchestrator

orch = Orchestrator(
    api_key="sk-...",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-v4-flash",
    project_root="/path/to/project",
)

ctx = orch.run(
    logs="...",              # 错误日志
    source_code="...",       # 相关源代码
    alert_payload="...",     # 告警 payload（可选）
    service_name="pay-svc",  # 服务名（部署用）
    skip_deploy=False,       # False = 执行外循环
)
```

### 方式二：高级模式

单独构建 Agent，精细控制每个环节（不同模型、不同 Provider）：

```python
from libs.agent import OpenAICompatibleAdapter
from libs.devops import Orchestrator
from libs.devops.agents import (
    BugAnalyzerAgent, BugRecurrenceAgent, TestDriverAgent,
    PlannerAgent, PlanReviewAgent, TDDExecutorAgent, CodeReviewerAgent,
)
from libs.devops.runners import SandboxRunner, PRGenerator
from libs.devops.deploy import ReleaseController, SREGuard

# 不同角色可以用不同模型
analysis_provider = OpenAICompatibleAdapter(
    api_key="sk-...", base_url="https://api.openai.com/v1", model="gpt-4o",
)
coding_provider = OpenAICompatibleAdapter(
    api_key="sk-...", base_url="https://api.deepseek.com/v1", model="deepseek-v4-flash",
)

orch = Orchestrator(
    bug_analyzer=BugAnalyzerAgent(provider=analysis_provider),
    bug_recurrence=BugRecurrenceAgent(provider=analysis_provider),
    test_driver=TestDriverAgent(provider=coding_provider),
    planner=PlannerAgent(provider=analysis_provider),
    plan_reviewer=PlanReviewAgent(provider=analysis_provider),
    tdd_executor=TDDExecutorAgent(provider=coding_provider),
    code_reviewer=CodeReviewerAgent(provider=analysis_provider),
    sandbox=SandboxRunner(
        project_root="/path/to/project",
        test_command="python -m pytest -x -q",
        lint_command="python -m ruff check .",
        mutation_command="python -m mutmut run",
    ),
    pr_generator=PRGenerator(repo_root="/path/to/repo"),
    release_controller=ReleaseController(observation_window_sec=300),
    sre_guard=SREGuard(prometheus_url="http://prometheus:9090"),
)
```

### 读取管线结果

```python
ctx = orch.run(logs="...")

run = ctx.run
print(f"状态: {run.state.value}")           # completed / human_escalation / rolled_back
print(f"Bug:  {run.bug_report.title}")      # Bug 标题
print(f"复现: {run.reproduction.reproduced}")# 是否复现
print(f"补丁: {len(run.patches)} 个")        # 生成了几个 Patch
print(f"PR:   {run.pr_info.pr_url}")        # PR 链接

# 熔断状态
for node, s in orch.get_breaker_status().items():
    if s["failures"] > 0:
        print(f"  {node}: {s['failures']}/{s['max_failures']} ({s['state']})")

# 持久化状态（自动保存到 .devops_state/{trace_id}.json）
ctx.save("my_state.json")
```

---

## 关键约束

1. **执行层零自由文本** — Agent 永远不能直接生成 `ssh` 或 `kubectl delete`，只能输出结构化 JSON，由代码守门员验证后执行。

2. **涉及线上流量的操作，决策必须是代码** — 扩缩容 (HPA/KEDA)、流量切换 (Service Mesh)、回滚 (SLO 触发) 全部由代码驱动。

3. **所有 Agent 输出都是"草稿"** — 必须经过编译器、测试引擎、OPA 才能生效。

4. **LLM 不碰闸刀** — `SREGuard.evaluate()` 是纯函数（无网络、无 LLM），毫秒级判定；LLM 只在事前写策略、事后读日志。

---

## 数据类型速查

| 类型 | 位置 | 说明 |
|---|---|---|
| `BugReport` | `types.py` | 结构化 Bug 报告（title / severity / stack_trace / root_cause） |
| `ReproductionResult` | `types.py` | 复现结果（script / reproduced / output） |
| `RedTest` | `types.py` | 红色测试（test_content / test_name） |
| `FixPlan` | `types.py` | 修复计划（strategy / files / max_lines / risk） |
| `PlanReviewResult` | `types.py` | 计划审查（verdict / concerns / suggestions） |
| `Patch` | `types.py` | 代码补丁（files_changed / diff） |
| `CodeReviewResult` | `types.py` | 代码审查（verdict / issues / hallucination_flags） |
| `SandboxResult` | `types.py` | 沙箱结果（compile / tests / lint / mutation / all_passed） |
| `PRInfo` | `types.py` | PR 信息（branch / url / commit_sha / image_tag） |
| `BuildResult` | `types.py` | 构建结果（image_tag / opa_check） |
| `CanaryMetrics` | `types.py` | 金丝雀指标（error_rate / p99 / slo） |
| `RolloutStatus` | `types.py` | 发布状态（stage / decision / metrics_history） |
| `EscalationRequest` | `types.py` | 人工升级请求（reason / failed_stage） |
| `PipelineRun` | `types.py` | 完整管线状态聚合 |
| `SharedContext` | `context.py` | 线程安全共享存储，支持 JSON 持久化 |

---

## 兼容的 API 服务

任何兼容 OpenAI Chat Completions 接口的服务均可使用：

| 服务 | base_url | model 示例 |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` / `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |
| Azure OpenAI | `https://{name}.openai.azure.com/openai/deployments/{deploy}/` | 按部署名 |
| Ollama | `http://localhost:11434/v1` | `llama3` / `qwen2` |
| vLLM | `http://localhost:8000/v1` | 按加载模型 |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/...` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |

---

## 依赖

| 包 | 版本 | 用途 |
|---|---|---|
| `httpx` | ≥ 0.28.1 | HTTP 客户端（LLM API 调用 + Prometheus 查询） |
| `rich` | ≥ 15.0.0 | 终端格式化输出 |

Python ≥ 3.12，构建工具：Hatch。

---

## License

MIT
