"""网络诊断工具 — 端口检查、DNS查询、HTTP状态检测"""

import asyncio
import socket
import time

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _check_port(host: str, port: int, timeout: float = 3.0) -> dict:
    """检查端口是否开放"""
    start = time.time()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        elapsed = (time.time() - start) * 1000
        sock.close()
        return {
            "port": port,
            "open": result == 0,
            "latency_ms": elapsed,
        }
    except (socket.gaierror, OSError) as e:
        return {"port": port, "open": False, "error": str(e), "latency_ms": 0}


def _port_scan(host: str):
    """扫描常用端口"""
    common_ports = {
        22: "SSH",
        80: "HTTP",
        443: "HTTPS",
        3306: "MySQL",
        5432: "PostgreSQL",
        6379: "Redis",
        8080: "HTTP-Alt",
        8443: "HTTPS-Alt",
        27017: "MongoDB",
    }

    console.print(f"\n[dim]正在扫描 {host} 的常用端口...[/]\n")

    table = Table(title=f"端口扫描: {host}")
    table.add_column("端口", style="cyan", justify="right")
    table.add_column("服务", width=12)
    table.add_column("状态")
    table.add_column("延迟", justify="right")

    for port, service in common_ports.items():
        result = _check_port(host, port)
        if result["open"]:
            status = "[green]开放[/]"
            latency = f"{result['latency_ms']:.0f}ms"
        else:
            status = "[red]关闭[/]"
            latency = "-"
        table.add_row(str(port), service, status, latency)

    console.print(table)


def _dns_lookup(domain: str):
    """DNS 查询"""
    console.print(f"\n[dim]正在查询 {domain}...[/]\n")

    table = Table(title=f"DNS 查询: {domain}")
    table.add_column("类型", style="cyan")
    table.add_column("结果")

    # A 记录
    try:
        ips = socket.getaddrinfo(domain, None, socket.AF_INET)
        seen = set()
        for info in ips:
            ip = info[4][0]
            if ip not in seen:
                seen.add(ip)
                table.add_row("A (IPv4)", ip)
    except socket.gaierror as e:
        table.add_row("A (IPv4)", f"[red]查询失败: {e}[/]")

    # AAAA 记录
    try:
        ips6 = socket.getaddrinfo(domain, None, socket.AF_INET6)
        seen6 = set()
        for info in ips6:
            ip = info[4][0]
            if ip not in seen6:
                seen6.add(ip)
                table.add_row("AAAA (IPv6)", ip)
    except socket.gaierror:
        table.add_row("AAAA (IPv6)", "[dim]无记录[/]")

    # 反向 DNS
    try:
        first_ip = socket.gethostbyname(domain)
        hostname = socket.gethostbyaddr(first_ip)
        table.add_row("PTR (反向)", hostname[0])
    except (socket.herror, socket.gaierror):
        table.add_row("PTR (反向)", "[dim]无记录[/]")

    console.print(table)


async def _http_check_async(urls: list[str]) -> list[dict]:
    """异步检查多个 URL 的 HTTP 状态"""
    results = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for url in urls:
            start = time.time()
            try:
                resp = await client.get(url)
                elapsed = (time.time() - start) * 1000
                results.append(
                    {
                        "url": url,
                        "status": resp.status_code,
                        "latency_ms": elapsed,
                        "size": len(resp.content),
                        "error": None,
                    }
                )
            except Exception as e:
                elapsed = (time.time() - start) * 1000
                results.append(
                    {
                        "url": url,
                        "status": 0,
                        "latency_ms": elapsed,
                        "size": 0,
                        "error": str(e)[:50],
                    }
                )
    return results


def _http_check(urls: list[str]):
    """检查 HTTP 状态"""
    console.print(f"\n[dim]正在检查 {len(urls)} 个 URL...[/]\n")

    results = asyncio.run(_http_check_async(urls))

    table = Table(title="HTTP 状态检查")
    table.add_column("URL", max_width=40)
    table.add_column("状态码", justify="center")
    table.add_column("延迟", justify="right")
    table.add_column("大小", justify="right")
    table.add_column("备注")

    for r in results:
        if r["error"]:
            status = "[red]错误[/]"
            note = f"[red]{r['error']}[/]"
        elif r["status"] < 300:
            status = f"[green]{r['status']}[/]"
            note = ""
        elif r["status"] < 400:
            status = f"[yellow]{r['status']}[/]"
            note = "重定向"
        else:
            status = f"[red]{r['status']}[/]"
            note = ""

        latency = f"{r['latency_ms']:.0f}ms"
        size = f"{r['size']:,} B" if r["size"] else "-"
        table.add_row(r["url"][:40], status, latency, size, note)

    console.print(table)


def run_netdiag():
    """网络诊断工具交互循环"""
    console.print(
        Panel(
            "[bold]网络诊断工具[/]\n"
            "[dim]命令: port <host> | dns <domain> | http <url,...> | quit[/]",
            border_style="yellow",
        )
    )

    while True:
        cmd = console.input("\n[yellow]net >[/] ").strip()

        if cmd.lower() in ("quit", "q", "exit"):
            break
        elif cmd.lower().startswith("port"):
            host = cmd[4:].strip()
            if not host:
                host = console.input("  目标主机: ").strip()
            if host:
                _port_scan(host)
            else:
                console.print("[red]请输入主机地址[/]")
        elif cmd.lower().startswith("dns"):
            domain = cmd[3:].strip()
            if not domain:
                domain = console.input("  域名: ").strip()
            if domain:
                _dns_lookup(domain)
            else:
                console.print("[red]请输入域名[/]")
        elif cmd.lower().startswith("http"):
            urls_str = cmd[4:].strip()
            if not urls_str:
                urls_str = console.input("  URL (逗号分隔): ").strip()
            if urls_str:
                urls = [u.strip() for u in urls_str.split(",") if u.strip()]
                # 自动补全 https://
                urls = [u if u.startswith("http") else f"https://{u}" for u in urls]
                _http_check(urls)
            else:
                console.print("[red]请输入 URL[/]")
        else:
            console.print("[red]未知命令，可用: port | dns | http | quit[/]")
