"""系统监控工具 — CPU/内存/磁盘信息，带彩色进度条"""

import os
import platform
import shutil
import time

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

console = Console()


def _get_cpu_percent() -> float:
    """简易 CPU 使用率估算（跨平台，无需 psutil）"""
    if platform.system() == "Windows":
        import subprocess

        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            for line in lines:
                if line.isdigit():
                    return float(line)
        except Exception:
            pass
    else:
        try:
            loads = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            return min(100.0, (loads[0] / cpu_count) * 100)
        except Exception:
            pass
    return 0.0


def _get_memory_info() -> dict:
    """获取内存信息"""
    if platform.system() == "Windows":
        import subprocess

        try:
            result = subprocess.run(
                [
                    "wmic",
                    "OS",
                    "get",
                    "TotalVisibleMemorySize,FreePhysicalMemory",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            for line in lines:
                parts = line.split()
                if len(parts) == 2 and parts[0].isdigit():
                    free_kb = int(parts[0])
                    total_kb = int(parts[1])
                    used_kb = total_kb - free_kb
                    return {
                        "total": total_kb * 1024,
                        "used": used_kb * 1024,
                        "percent": (used_kb / total_kb) * 100 if total_kb else 0,
                    }
        except Exception:
            pass
    else:
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split()
                    if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                        info[parts[0][:-1]] = int(parts[1]) * 1024
                total = info.get("MemTotal", 0)
                available = info.get("MemAvailable", info.get("MemFree", 0))
                used = total - available
                return {
                    "total": total,
                    "used": used,
                    "percent": (used / total) * 100 if total else 0,
                }
        except Exception:
            pass
    return {"total": 0, "used": 0, "percent": 0}


def _get_disk_info() -> list[dict]:
    """获取磁盘分区信息"""
    disks = []
    if platform.system() == "Windows":
        import string

        for letter in string.ascii_uppercase:
            path = f"{letter}:\\"
            try:
                usage = shutil.disk_usage(path)
                if usage.total > 0:
                    disks.append(
                        {
                            "mount": path,
                            "total": usage.total,
                            "used": usage.used,
                            "free": usage.free,
                            "percent": (usage.used / usage.total) * 100,
                        }
                    )
            except (OSError, PermissionError):
                continue
    else:
        try:
            usage = shutil.disk_usage("/")
            disks.append(
                {
                    "mount": "/",
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": (usage.used / usage.total) * 100,
                }
            )
        except Exception:
            pass
    return disks


def _format_bytes(b: int) -> str:
    """格式化字节数"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _color_for_percent(pct: float) -> str:
    if pct >= 90:
        return "red"
    elif pct >= 70:
        return "yellow"
    return "green"


def _build_dashboard() -> Panel:
    """构建系统监控面板"""
    # CPU
    cpu_pct = _get_cpu_percent()
    cpu_color = _color_for_percent(cpu_pct)

    # Memory
    mem = _get_memory_info()
    mem_color = _color_for_percent(mem["percent"])

    # Disk
    disks = _get_disk_info()

    # 构建表格
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Label", width=12)
    table.add_column("Bar", min_width=30)
    table.add_column("Info", min_width=20)

    # CPU 行
    cpu_bar = f"[{cpu_color}]{'█' * int(cpu_pct / 5)}{'░' * (20 - int(cpu_pct / 5))}[/]"
    table.add_row("[bold]CPU[/]", cpu_bar, f"[{cpu_color}]{cpu_pct:.1f}%[/]")

    # Memory 行
    mem_bar = f"[{mem_color}]{'█' * int(mem['percent'] / 5)}{'░' * (20 - int(mem['percent'] / 5))}[/]"
    mem_info = f"[{mem_color}]{mem['percent']:.1f}%[/] ({_format_bytes(mem['used'])}/{_format_bytes(mem['total'])})"
    table.add_row("[bold]内存[/]", mem_bar, mem_info)

    # Disk 行
    for disk in disks[:4]:  # 最多显示4个分区
        d_color = _color_for_percent(disk["percent"])
        d_bar = f"[{d_color}]{'█' * int(disk['percent'] / 5)}{'░' * (20 - int(disk['percent'] / 5))}[/]"
        d_info = f"[{d_color}]{disk['percent']:.1f}%[/] ({_format_bytes(disk['used'])}/{_format_bytes(disk['total'])})"
        table.add_row(f"[bold]{disk['mount']}[/]", d_bar, d_info)

    # 系统信息
    sys_info = (
        f"\n[dim]系统: {platform.system()} {platform.release()} | "
        f"架构: {platform.machine()} | "
        f"CPU核心: {os.cpu_count()}[/]"
    )

    from rich.text import Text

    content = table
    return Panel(
        content,
        title="[bold green]💻 系统监控[/]",
        subtitle=f"[dim]{platform.system()} {platform.release()} | {platform.machine()} | {os.cpu_count()} cores[/]",
        border_style="green",
    )


def run_sysmon():
    """系统监控工具"""
    console.print("[dim]按 Ctrl+C 退出监控...[/]\n")

    try:
        with Live(_build_dashboard(), console=console, refresh_per_second=1) as live:
            while True:
                time.sleep(2)
                live.update(_build_dashboard())
    except KeyboardInterrupt:
        console.print("\n[dim]监控已停止[/]")
