import os
import signal
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


def start_systemd_inhibit(console: Console) -> subprocess.Popen[bytes] | None:
    if sys.platform != "linux":
        return None
    try:
        return subprocess.Popen(
            [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=gattv camera",
                "--why=Camera monitoring is running",
                "--mode=block",
                "sleep",
                "infinity",
            ],
            start_new_session=True,
        )
    except FileNotFoundError:
        console.print(
            "[yellow]Could not find systemd-inhibit; sleep is not prevented.[/]"
        )
        return None


def stop_systemd_inhibit(inhibitor: subprocess.Popen[bytes] | None) -> None:
    if inhibitor is None or inhibitor.poll() is not None:
        return
    try:
        os.killpg(inhibitor.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        inhibitor.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(inhibitor.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
