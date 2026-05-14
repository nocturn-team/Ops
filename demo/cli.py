"""CLI Demo — 多功能命令行工具集

包含: Todo管理 | 系统监控 | 文件管理 | 网络诊断
"""

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def show_banner():
    """显示欢迎横幅"""
    banner = Panel(
        "[bold cyan]CLI 工具集 Demo[/]\n\n"
        "[dim]1.[/] 📋 Todo 任务管理器\n"
        "[dim]2.[/] 💻 系统监控工具\n"
        "[dim]3.[/] 📁 文件管理工具\n"
        "[dim]4.[/] 🌐 网络诊断工具\n"
        "[dim]0.[/] 退出",
        title="[bold green]DevOps Toolkit[/]",
        border_style="blue",
    )
    console.print(banner)


def main():
    """主入口"""
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        dispatch_command(cmd)
        return

    # 交互模式
    while True:
        show_banner()
        choice = console.input("\n[bold yellow]请选择功能 >[/] ").strip()

        if choice == "0":
            console.print("[dim]再见！[/]")
            break
        elif choice == "1":
            from demo.todo import run_todo

            run_todo()
        elif choice == "2":
            from demo.sysmon import run_sysmon

            run_sysmon()
        elif choice == "3":
            from demo.filemgr import run_filemgr

            run_filemgr()
        elif choice == "4":
            from demo.netdiag import run_netdiag

            run_netdiag()
        else:
            console.print("[red]无效选择，请重试[/]")

        console.input("\n[dim]按 Enter 返回主菜单...[/]")
        console.clear()


def dispatch_command(cmd: str):
    """根据命令行参数直接调用子模块"""
    commands = {
        "todo": ("demo.todo", "run_todo"),
        "sysmon": ("demo.sysmon", "run_sysmon"),
        "filemgr": ("demo.filemgr", "run_filemgr"),
        "netdiag": ("demo.netdiag", "run_netdiag"),
    }

    if cmd == "help":
        table = Table(title="可用命令")
        table.add_column("命令", style="cyan")
        table.add_column("说明")
        table.add_row("todo", "Todo 任务管理器")
        table.add_row("sysmon", "系统监控工具")
        table.add_row("filemgr", "文件管理工具")
        table.add_row("netdiag", "网络诊断工具")
        table.add_row("help", "显示帮助")
        console.print(table)
        return

    if cmd not in commands:
        console.print(f"[red]未知命令: {cmd}[/]，使用 'help' 查看可用命令")
        return

    module_name, func_name = commands[cmd]
    import importlib

    module = importlib.import_module(module_name)
    getattr(module, func_name)()


if __name__ == "__main__":
    main()
