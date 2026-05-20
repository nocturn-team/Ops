"""DevOps Agent Demo — 展示双环收敛架构的 Web 可视化

演示 9 阶段管道的完整运行过程：
  Bug分析 → 复现 → 红测试 → 规划 → 评审 → 内环TDD → PR → CI → 金丝雀部署

运行:
  python -m demo.web.app          (从项目根目录)
  python demo/web/app.py          (从项目根目录)
  cd demo/web && python app.py    (从子目录)

访问: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="DevOps Agent Demo", version="1.0.0")

# ─── 全局状态 ────────────────────────────────────────────────────────────────────

STATE_DIR = Path(".devops_state")
active_runs: dict[str, dict] = {}  # trace_id -> run state dict
ws_clients: list[WebSocket] = []


# ─── WebSocket 广播 ──────────────────────────────────────────────────────────────


async def broadcast(event: dict):
    """向所有连接的客户端广播事件"""
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)


# ─── 模拟管道运行 ────────────────────────────────────────────────────────────────


async def simulate_pipeline(trace_id: str, bug_description: str):
    """模拟完整的 9 阶段管道运行，通过 WebSocket 实时推送进度"""

    async def emit(stage: str, status: str, data: dict | None = None, delay: float = 0.8):
        event = {
            "type": "stage_update",
            "trace_id": trace_id,
            "stage": stage,
            "status": status,
            "data": data or {},
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        active_runs[trace_id]["events"].append(event)
        active_runs[trace_id]["current_stage"] = stage
        active_runs[trace_id]["current_status"] = status
        await broadcast(event)
        await asyncio.sleep(delay)

    async def emit_agent(agent: str, action: str, content: str, delay: float = 0.5):
        event = {
            "type": "agent_activity",
            "trace_id": trace_id,
            "agent": agent,
            "action": action,
            "content": content,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        active_runs[trace_id]["events"].append(event)
        await broadcast(event)
        await asyncio.sleep(delay)

    async def emit_breaker(node: str, failures: int, max_f: int, state: str):
        event = {
            "type": "breaker_update",
            "trace_id": trace_id,
            "node": node,
            "failures": failures,
            "max_failures": max_f,
            "state": state,
        }
        await broadcast(event)

    # 初始化运行状态
    active_runs[trace_id] = {
        "trace_id": trace_id,
        "bug_description": bug_description,
        "current_stage": "idle",
        "current_status": "pending",
        "started_at": datetime.now().isoformat(),
        "events": [],
    }

    await broadcast({
        "type": "pipeline_start",
        "trace_id": trace_id,
        "bug_description": bug_description,
    })

    # ── Stage 1: Bug Analysis ──
    await emit("bug_analysis", "running")
    await emit_agent("BugAnalyzer", "thinking", "分析日志和错误堆栈...")
    await emit_agent("BugAnalyzer", "tool_call", "parse_stack_trace(logs)")
    await asyncio.sleep(1.0)
    await emit_agent("BugAnalyzer", "result", json.dumps({
        "title": "NullPointerException in PaymentService.processRefund()",
        "severity": "high",
        "affected_service": "payment-service",
        "root_cause": "未检查 refundRequest.getOriginalTransaction() 返回值",
    }, ensure_ascii=False))
    await emit("bug_analysis", "completed", {
        "title": "NullPointerException in PaymentService.processRefund()",
        "severity": "high",
        "affected_files": ["src/payment/service.py", "src/payment/models.py"],
        "root_cause": "未检查 refundRequest.getOriginalTransaction() 返回值",
    })

    # ── Stage 2: Bug Recurrence ──
    await emit("bug_recurrence", "running")
    await emit_agent("BugRecurrence", "thinking", "生成复现脚本...")
    await emit_agent("BugRecurrence", "tool_call", "sandbox.run_script(repro_test.py)")
    await asyncio.sleep(1.2)
    await emit_agent("BugRecurrence", "result", "✅ Bug 已复现 — NullPointerException 在第 47 行触发")
    await emit_breaker("bug_recurrence", 0, 3, "closed")
    await emit("bug_recurrence", "completed", {
        "reproduced": True,
        "attempts": 1,
        "output": "AssertionError: processRefund() raised NullPointerException",
    })

    # ── Stage 3: Test Driving ──
    await emit("test_driving", "running")
    await emit_agent("TestDriver", "thinking", "编写红测试定义正确性边界...")
    await asyncio.sleep(1.0)
    await emit_agent("TestDriver", "result", "生成 2 个红测试:\n• test_refund_with_null_transaction\n• test_refund_partial_amount_null_check")
    await emit("test_driving", "completed", {
        "tests_count": 2,
        "tests": [
            "test_refund_with_null_transaction",
            "test_refund_partial_amount_null_check",
        ],
    })

    # ── Stage 4: Planning ──
    await emit("planning", "running")
    await emit_agent("Planner", "thinking", "制定修复计划...")
    await asyncio.sleep(1.0)
    await emit_agent("Planner", "result", json.dumps({
        "strategy": "添加空值检查 + 优雅降级",
        "files_to_modify": ["src/payment/service.py"],
        "max_lines_changed": 12,
        "risk": "low",
    }, ensure_ascii=False))
    await emit("planning", "completed", {
        "strategy": "添加空值检查 + 优雅降级",
        "files": ["src/payment/service.py"],
        "max_lines": 12,
        "risk": "low",
    })

    # ── Stage 5: Plan Review ──
    await emit("plan_review", "running")
    await emit_agent("PlanReviewer", "thinking", "对抗性审查修复计划...")
    await asyncio.sleep(1.0)
    await emit_agent("PlanReviewer", "result", "✅ APPROVE — 计划合理，风险可控")
    await emit_breaker("plan_review", 0, 2, "closed")
    await emit("plan_review", "completed", {"verdict": "approve"})

    # ── Stage 6: Inner Loop (Correctness) ──
    await emit("inner_loop", "running")

    # Iteration 1
    await emit_agent("TDD-Executor", "thinking", "编写修复补丁 (iteration 1)...")
    await asyncio.sleep(1.0)
    await emit_agent("TDD-Executor", "result", "补丁: 在 processRefund() 第 45 行添加 null check")

    await broadcast({
        "type": "patch_diff",
        "trace_id": trace_id,
        "iteration": 1,
        "file": "src/payment/service.py",
        "diff": (
            "@@ -42,8 +42,12 @@ class PaymentService:\n"
            "     def process_refund(self, refund_request: RefundRequest) -> RefundResult:\n"
            "         \"\"\"Process a refund for a completed transaction.\"\"\"\n"
            "         original_txn = refund_request.get_original_transaction()\n"
            "-        amount = original_txn.amount\n"
            "-        refund_id = self._gateway.initiate_refund(original_txn.id, amount)\n"
            "+        if original_txn is None:\n"
            "+            raise InvalidRefundError(\n"
            "+                f\"Original transaction not found for refund {refund_request.id}\"\n"
            "+            )\n"
            "+\n"
            "+        amount = original_txn.amount\n"
            "+        refund_id = self._gateway.initiate_refund(original_txn.id, amount)\n"
            "         return RefundResult(refund_id=refund_id, status=\"pending\")\n"
        ),
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })

    await emit_agent("CodeReviewer", "thinking", "审查补丁...")
    await asyncio.sleep(0.8)
    await emit_agent("CodeReviewer", "result", "⚠️ REQUEST_CHANGES — 建议添加日志记录")
    await emit_breaker("code_reviewer", 0, 2, "closed")

    await emit_agent("SandboxRunner", "tool_call", "pytest + ruff + mutation testing")
    await asyncio.sleep(1.5)
    await emit_agent("SandboxRunner", "result", "❌ 1/2 tests passed, mutation score: 0.6")
    await emit_breaker("sandbox_runner", 1, 2, "closed")

    await broadcast({
        "type": "inner_loop_iteration",
        "trace_id": trace_id,
        "iteration": 1,
        "max_iterations": 3,
        "tests_passed": False,
        "mutation_score": 0.6,
    })

    # Iteration 2
    await emit_agent("TDD-Executor", "thinking", "修订补丁 (iteration 2)...")
    await asyncio.sleep(1.0)
    await emit_agent("TDD-Executor", "result", "补丁v2: 添加日志 + 修复边界条件")

    await broadcast({
        "type": "patch_diff",
        "trace_id": trace_id,
        "iteration": 2,
        "file": "src/payment/service.py",
        "diff": (
            "@@ -42,12 +42,16 @@ class PaymentService:\n"
            "     def process_refund(self, refund_request: RefundRequest) -> RefundResult:\n"
            "         \"\"\"Process a refund for a completed transaction.\"\"\"\n"
            "         original_txn = refund_request.get_original_transaction()\n"
            "+\n"
            "         if original_txn is None:\n"
            "-            raise InvalidRefundError(\n"
            "-                f\"Original transaction not found for refund {refund_request.id}\"\n"
            "-            )\n"
            "+            logger.warning(\n"
            "+                \"Refund %s: original transaction is None, rejecting\",\n"
            "+                refund_request.id,\n"
            "+            )\n"
            "+            return RefundResult(\n"
            "+                refund_id=None, status=\"rejected\", reason=\"original_txn_missing\"\n"
            "+            )\n"
            " \n"
            "         amount = original_txn.amount\n"
            "+        logger.info(\"Processing refund %s for amount %s\", refund_request.id, amount)\n"
            "         refund_id = self._gateway.initiate_refund(original_txn.id, amount)\n"
            "         return RefundResult(refund_id=refund_id, status=\"pending\")\n"
        ),
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })

    await emit_agent("CodeReviewer", "thinking", "审查补丁 v2...")
    await asyncio.sleep(0.8)
    await emit_agent("CodeReviewer", "result", "✅ APPROVE — 代码质量良好")
    await emit_breaker("code_reviewer", 0, 2, "closed")

    await emit_agent("SandboxRunner", "tool_call", "pytest + ruff + mutation testing")
    await asyncio.sleep(1.5)
    await emit_agent("SandboxRunner", "result", "✅ 2/2 tests passed, lint OK, mutation score: 0.92")
    await emit_breaker("sandbox_runner", 0, 2, "closed")

    await broadcast({
        "type": "inner_loop_iteration",
        "trace_id": trace_id,
        "iteration": 2,
        "max_iterations": 3,
        "tests_passed": True,
        "mutation_score": 0.92,
    })

    await emit("inner_loop", "completed", {
        "iterations": 2,
        "converged": True,
        "final_mutation_score": 0.92,
    })

    # ── Stage 7: PR Generation ──
    await emit("pr_generation", "running")
    await emit_agent("PRGenerator", "tool_call", "git checkout -b fix/null-check-refund")
    await asyncio.sleep(0.5)
    await emit_agent("PRGenerator", "tool_call", "gh pr create --title 'fix: null check in processRefund'")
    await asyncio.sleep(0.8)
    await emit("pr_generation", "completed", {
        "branch": "fix/null-check-refund",
        "pr_url": "https://github.com/org/payment-service/pull/142",
        "pr_number": 142,
    })

    # ── Stage 8: CI Build ──
    await emit("ci_build", "running")
    await emit_agent("CI", "tool_call", "docker build + OPA policy check")
    await asyncio.sleep(1.2)
    await emit("ci_build", "completed", {
        "image_tag": f"sha-{trace_id[:12]}",
        "build_ok": True,
        "opa_passed": True,
    })

    # ── Stage 9: Outer Loop (Canary) ──
    await emit("outer_loop", "running")

    for stage, pct in [("5%", 5), ("25%", 25), ("50%", 50), ("100%", 100)]:
        await emit_agent("ReleaseController", "tool_call", f"shift_traffic({stage})")
        await asyncio.sleep(0.8)
        await emit_agent("SREGuard", "thinking", f"观测 {stage} 流量指标...")
        await asyncio.sleep(1.0)
        metrics = {
            "stage": stage,
            "error_rate": round(0.01 * (pct / 100), 4),
            "p99_latency_ms": 120 + pct * 0.5,
            "decision": "promote",
        }
        await emit_agent("SREGuard", "result", f"✅ PROMOTE — error_rate={metrics['error_rate']}%, P99={metrics['p99_latency_ms']:.0f}ms")
        await broadcast({
            "type": "canary_progress",
            "trace_id": trace_id,
            "stage": stage,
            "metrics": metrics,
        })

    await emit_breaker("canary_5", 0, 1, "closed")
    await emit_breaker("canary_25", 0, 1, "closed")
    await emit("outer_loop", "completed", {"final_traffic": "100%", "decision": "promote"})

    # ── Pipeline Complete ──
    active_runs[trace_id]["current_stage"] = "completed"
    active_runs[trace_id]["current_status"] = "completed"
    active_runs[trace_id]["finished_at"] = datetime.now().isoformat()

    await broadcast({
        "type": "pipeline_complete",
        "trace_id": trace_id,
        "duration_display": "模拟完成",
    })


# ─── API 路由 ────────────────────────────────────────────────────────────────────


@app.post("/api/pipeline/trigger")
async def trigger_pipeline(body: dict):
    """触发一次管道运行（模拟模式）"""
    trace_id = uuid.uuid4().hex[:12]
    bug_desc = body.get("description", "NullPointerException in PaymentService")

    # 后台运行模拟
    asyncio.create_task(simulate_pipeline(trace_id, bug_desc))

    return {"trace_id": trace_id, "status": "started"}


@app.get("/api/pipeline/runs")
async def list_runs():
    """列出所有运行"""
    return [
        {
            "trace_id": r["trace_id"],
            "bug_description": r["bug_description"],
            "current_stage": r["current_stage"],
            "current_status": r["current_status"],
            "started_at": r["started_at"],
        }
        for r in active_runs.values()
    ]


@app.get("/api/pipeline/runs/{trace_id}")
async def get_run(trace_id: str):
    """获取单次运行详情"""
    if trace_id not in active_runs:
        return {"error": "not found"}
    return active_runs[trace_id]


@app.get("/api/pipeline/history")
async def list_history():
    """列出历史运行（从 .devops_state/ 加载）"""
    results = []
    if STATE_DIR.exists():
        for f in STATE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                run = data.get("run", {})
                results.append({
                    "trace_id": run.get("trace_id", f.stem),
                    "state": run.get("state", "unknown"),
                    "created_at": run.get("created_at", ""),
                    "bug_title": run.get("bug_report", {}).get("title", "") if run.get("bug_report") else "",
                })
            except Exception:
                pass
    return results


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点 — 实时推送管道事件"""
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            # 保持连接，接收客户端心跳
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_clients.remove(websocket)


# ─── 前端页面 ────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ─── 启动 ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print("🚀 DevOps Agent Demo")
    print("📍 http://localhost:8000")
    print("📖 API: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
