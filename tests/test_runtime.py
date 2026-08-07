from unittest.mock import Mock, patch

from rich.console import Console

from gattv.runtime import start_caffeinate, stop_caffeinate


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
