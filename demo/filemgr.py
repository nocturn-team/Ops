"""文件管理工具 — 批量重命名、目录统计、文件搜索"""

import os
import re
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

console = Console()


def _format_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _dir_stats(path: Path) -> dict:
    """统计目录信息"""
    stats = {"files": 0, "dirs": 0, "total_size": 0, "by_ext": {}}

    try:
        for item in path.rglob("*"):
            if item.is_file():
                stats["files"] += 1
                size = item.stat().st_size
                stats["total_size"] += size
                ext = item.suffix.lower() or "(无扩展名)"
                if ext not in stats["by_ext"]:
                    stats["by_ext"][ext] = {"count": 0, "size": 0}
                stats["by_ext"][ext]["count"] += 1
                stats["by_ext"][ext]["size"] += size
            elif item.is_dir():
                stats["dirs"] += 1
    except PermissionError:
        pass

    return stats


def _show_dir_stats(path: Path):
    """显示目录统计"""
    console.print(f"\n[dim]正在扫描: {path}...[/]")
    stats = _dir_stats(path)

    console.print(
        Panel(
            f"[bold]文件数:[/] {stats['files']}\n"
            f"[bold]目录数:[/] {stats['dirs']}\n"
            f"[bold]总大小:[/] {_format_size(stats['total_size'])}",
            title=f"[bold]📁 {path.name or path}[/]",
            border_style="blue",
        )
    )

    if stats["by_ext"]:
        table = Table(title="按扩展名统计")
        table.add_column("扩展名", style="cyan")
        table.add_column("文件数", justify="right")
        table.add_column("大小", justify="right")

        sorted_exts = sorted(stats["by_ext"].items(), key=lambda x: x[1]["size"], reverse=True)
        for ext, info in sorted_exts[:15]:
            table.add_row(ext, str(info["count"]), _format_size(info["size"]))

        console.print(table)


def _batch_rename(path: Path):
    """批量重命名"""
    console.print("\n[bold]批量重命名[/]")
    console.print("[dim]支持正则表达式替换文件名[/]\n")

    pattern = console.input("  查找模式 (正则): ").strip()
    replacement = console.input("  替换为: ").strip()
    ext_filter = console.input("  文件扩展名过滤 (如 .txt，留空=全部): ").strip()

    if not pattern:
        console.print("[red]模式不能为空[/]")
        return

    try:
        regex = re.compile(pattern)
    except re.error as e:
        console.print(f"[red]正则表达式错误: {e}[/]")
        return

    # 预览
    matches = []
    for item in path.iterdir():
        if not item.is_file():
            continue
        if ext_filter and item.suffix.lower() != ext_filter.lower():
            continue
        new_name = regex.sub(replacement, item.stem) + item.suffix
        if new_name != item.name:
            matches.append((item, item.parent / new_name))

    if not matches:
        console.print("[yellow]没有匹配的文件[/]")
        return

    table = Table(title="预览重命名")
    table.add_column("原文件名", style="red")
    table.add_column("新文件名", style="green")

    for old, new in matches[:20]:
        table.add_row(old.name, new.name)

    if len(matches) > 20:
        table.add_row("...", f"(共 {len(matches)} 个文件)")

    console.print(table)

    confirm = console.input("\n  确认执行? (y/N): ").strip().lower()
    if confirm == "y":
        for old, new in matches:
            old.rename(new)
        console.print(f"[green]已重命名 {len(matches)} 个文件[/]")
    else:
        console.print("[dim]已取消[/]")


def _find_files(path: Path):
    """搜索文件"""
    pattern = console.input("\n  搜索文件名 (支持通配符): ").strip()
    if not pattern:
        console.print("[red]搜索模式不能为空[/]")
        return

    console.print(f"[dim]正在搜索: {pattern}...[/]")

    results = list(path.rglob(pattern))[:50]

    if not results:
        console.print("[yellow]未找到匹配文件[/]")
        return

    table = Table(title=f"搜索结果 ({len(results)} 个)")
    table.add_column("文件", style="cyan")
    table.add_column("大小", justify="right")
    table.add_column("路径", style="dim")

    for item in results:
        if item.is_file():
            size = _format_size(item.stat().st_size)
        else:
            size = "[dir]"
        rel = str(item.relative_to(path))
        table.add_row(item.name, size, rel)

    console.print(table)


def _show_tree(path: Path, max_depth: int = 3):
    """显示目录树"""
    tree = Tree(f"📁 [bold]{path.name or path}[/]")

    def _add_items(parent_tree, parent_path: Path, depth: int):
        if depth >= max_depth:
            return
        try:
            items = sorted(parent_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            for item in items[:20]:
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    branch = parent_tree.add(f"📁 [blue]{item.name}[/]")
                    _add_items(branch, item, depth + 1)
                else:
                    size = _format_size(item.stat().st_size)
                    parent_tree.add(f"📄 {item.name} [dim]({size})[/]")
            if len(list(parent_path.iterdir())) > 20:
                parent_tree.add("[dim]... 更多文件[/]")
        except PermissionError:
            parent_tree.add("[red]权限不足[/]")

    _add_items(tree, path, 0)
    console.print(tree)


def run_filemgr():
    """文件管理工具交互循环"""
    console.print(
        Panel(
            "[bold]文件管理工具[/]\n"
            "[dim]命令: stats | rename | find | tree | cd <path> | quit[/]",
            border_style="magenta",
        )
    )

    cwd = Path.cwd()

    while True:
        console.print(f"\n[dim]当前目录: {cwd}[/]")
        cmd = console.input("[magenta]file >[/] ").strip().lower()

        if cmd in ("quit", "q", "exit"):
            break
        elif cmd == "stats":
            _show_dir_stats(cwd)
        elif cmd == "rename":
            _batch_rename(cwd)
        elif cmd == "find":
            _find_files(cwd)
        elif cmd == "tree":
            _show_tree(cwd)
        elif cmd.startswith("cd"):
            target = cmd[2:].strip()
            if not target:
                target = console.input("  目标路径: ").strip()
            new_path = Path(target).expanduser()
            if not new_path.is_absolute():
                new_path = cwd / new_path
            if new_path.is_dir():
                cwd = new_path.resolve()
            else:
                console.print(f"[red]目录不存在: {new_path}[/]")
        else:
            console.print("[red]未知命令[/]")
