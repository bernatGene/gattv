import subprocess
import sys

from rich.console import Console


def start_caffeinate(console: Console) -> subprocess.Popen[bytes] | None:
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i"])
    except FileNotFoundError:
        console.print("[yellow]Could not find caffeinate; sleep is not prevented.[/]")
        return None


def stop_caffeinate(caffeinate: subprocess.Popen[bytes] | None) -> None:
    if caffeinate is None or caffeinate.poll() is not None:
        return
    caffeinate.terminate()
    try:
        caffeinate.wait(timeout=2)
    except subprocess.TimeoutExpired:
        caffeinate.kill()
