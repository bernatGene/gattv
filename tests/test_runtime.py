from unittest.mock import Mock, patch

from rich.console import Console

from gattv.runtime import (
    start_caffeinate,
    start_systemd_inhibit,
    stop_caffeinate,
    stop_systemd_inhibit,
)


def test_start_caffeinate_only_runs_on_macos() -> None:
    with (
        patch("gattv.runtime.sys.platform", "linux"),
        patch("gattv.runtime.subprocess.Popen") as popen,
    ):
        assert start_caffeinate(Console()) is None
    popen.assert_not_called()


def test_stop_caffeinate_terminates_running_process() -> None:
    process = Mock()
    process.poll.return_value = None

    stop_caffeinate(process)

    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=2)


def test_start_systemd_inhibit_runs_on_linux() -> None:
    with (
        patch("gattv.runtime.sys.platform", "linux"),
        patch("gattv.runtime.subprocess.Popen") as popen,
    ):
        assert start_systemd_inhibit(Console()) is popen.return_value

    popen.assert_called_once_with(
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


def test_start_systemd_inhibit_only_runs_on_linux() -> None:
    with (
        patch("gattv.runtime.sys.platform", "darwin"),
        patch("gattv.runtime.subprocess.Popen") as popen,
    ):
        assert start_systemd_inhibit(Console()) is None
    popen.assert_not_called()


def test_stop_systemd_inhibit_terminates_process_group() -> None:
    process = Mock(pid=123)
    process.poll.return_value = None

    with patch("gattv.runtime.os.killpg") as killpg:
        stop_systemd_inhibit(process)

    killpg.assert_called_once_with(123, 15)
    process.wait.assert_called_once_with(timeout=2)
