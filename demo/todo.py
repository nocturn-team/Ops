"""Todo 任务管理器 — 增删改查，JSON 持久化存储"""

import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
TODO_FILE = Path(__file__).parent / ".todos.json"


def _load_todos() -> list[dict]:
    if TODO_FILE.exists():
        return json.loads(TODO_FILE.read_text(encoding="utf-8"))
    return []


def _save_todos(todos: list[dict]):
    TODO_FILE.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_todos(todos: list[dict]):
    if not todos:
        console.print("[dim]暂无任务[/]")
        return

    table = Table(title="📋 任务列表", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("状态", width=4)
    table.add_column("任务", min_width=20)
    table.add_column("优先级", width=6)
    table.add_column("创建时间", style="dim")

    priority_colors = {"高": "red", "中": "yellow", "低": "green"}

    for i, t in enumerate(todos, 1):
        status = "✅" if t.get("done") else "⬜"
        name = f"[strike]{t['name']}[/]" if t.get("done") else t["name"]
        pri = t.get("priority", "中")
        color = priority_colors.get(pri, "white")
        table.add_row(str(i), status, name, f"[{color}]{pri}[/]", t.get("created", ""))

    console.print(table)


def run_todo():
    """Todo 管理器交互循环"""
    todos = _load_todos()

    console.print(
        Panel(
            "[bold]Todo 管理器[/]\n"
            "[dim]命令: list | add | done <n> | del <n> | clear | quit[/]",
            border_style="cyan",
        )
    )

    while True:
        _list_todos(todos)
        cmd = console.input("\n[cyan]todo >[/] ").strip().lower()

        if cmd in ("quit", "q", "exit"):
            _save_todos(todos)
            break
        elif cmd in ("list", "ls", "l"):
            continue
        elif cmd.startswith("add"):
            parts = cmd[3:].strip()
            if not parts:
                parts = console.input("  任务名称: ").strip()
            if not parts:
                console.print("[red]任务名称不能为空[/]")
                continue
            pri = console.input("  优先级 (高/中/低) [中]: ").strip() or "中"
            if pri not in ("高", "中", "低"):
                pri = "中"
            todos.append(
                {
                    "name": parts,
                    "done": False,
                    "priority": pri,
                    "created": datetime.now().strftime("%m-%d %H:%M"),
                }
            )
            _save_todos(todos)
            console.print(f"[green]已添加:[/] {parts}")
        elif cmd.startswith("done"):
            try:
                idx = int(cmd.split()[1]) - 1
                todos[idx]["done"] = True
                _save_todos(todos)
                console.print(f"[green]已完成:[/] {todos[idx]['name']}")
            except (IndexError, ValueError):
                console.print("[red]用法: done <编号>[/]")
        elif cmd.startswith("del"):
            try:
                idx = int(cmd.split()[1]) - 1
                removed = todos.pop(idx)
                _save_todos(todos)
                console.print(f"[yellow]已删除:[/] {removed['name']}")
            except (IndexError, ValueError):
                console.print("[red]用法: del <编号>[/]")
        elif cmd == "clear":
            todos = [t for t in todos if not t.get("done")]
            _save_todos(todos)
            console.print("[yellow]已清除所有已完成任务[/]")
        else:
            console.print("[red]未知命令[/]")
