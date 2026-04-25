"""Regression tests for terminal tool command handling."""

import json
from unittest.mock import MagicMock, patch

from gateway.session_context import clear_session_vars, set_session_vars
import tools.terminal_tool as terminal_tool


def _make_env_config(**overrides):
    config = {
        "env_type": "local",
        "timeout": 180,
        "cwd": "/tmp",
        "host_cwd": None,
        "modal_mode": "auto",
        "docker_image": "",
        "singularity_image": "",
        "modal_image": "",
        "daytona_image": "",
    }
    config.update(overrides)
    return config


def setup_function():
    terminal_tool._reset_cached_sudo_passwords()


def teardown_function():
    terminal_tool._reset_cached_sudo_passwords()


def test_searching_for_sudo_does_not_trigger_rewrite(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    command = "rg --line-number --no-heading --with-filename 'sudo' . | head -n 20"
    transformed, sudo_stdin = terminal_tool._transform_sudo_command(command)

    assert transformed == command
    assert sudo_stdin is None


def test_printf_literal_sudo_does_not_trigger_rewrite(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    command = "printf '%s\\n' sudo"
    transformed, sudo_stdin = terminal_tool._transform_sudo_command(command)

    assert transformed == command
    assert sudo_stdin is None


def test_non_command_argument_named_sudo_does_not_trigger_rewrite(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    command = "grep -n sudo README.md"
    transformed, sudo_stdin = terminal_tool._transform_sudo_command(command)

    assert transformed == command
    assert sudo_stdin is None


def test_actual_sudo_command_uses_configured_password(monkeypatch):
    monkeypatch.setenv("SUDO_PASSWORD", "testpass")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    transformed, sudo_stdin = terminal_tool._transform_sudo_command("sudo apt install -y ripgrep")

    assert transformed == "sudo -S -p '' apt install -y ripgrep"
    assert sudo_stdin == "testpass\n"


def test_actual_sudo_after_leading_env_assignment_is_rewritten(monkeypatch):
    monkeypatch.setenv("SUDO_PASSWORD", "testpass")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    transformed, sudo_stdin = terminal_tool._transform_sudo_command("DEBUG=1 sudo whoami")

    assert transformed == "DEBUG=1 sudo -S -p '' whoami"
    assert sudo_stdin == "testpass\n"


def test_explicit_empty_sudo_password_tries_empty_without_prompt(monkeypatch):
    monkeypatch.setenv("SUDO_PASSWORD", "")
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")

    def _fail_prompt(*_args, **_kwargs):
        raise AssertionError("interactive sudo prompt should not run for explicit empty password")

    monkeypatch.setattr(terminal_tool, "_prompt_for_sudo_password", _fail_prompt)

    transformed, sudo_stdin = terminal_tool._transform_sudo_command("sudo true")

    assert transformed == "sudo -S -p '' true"
    assert sudo_stdin == "\n"


def test_cached_sudo_password_is_used_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    terminal_tool._set_cached_sudo_password("cached-pass")

    transformed, sudo_stdin = terminal_tool._transform_sudo_command("echo ok && sudo whoami")

    assert transformed == "echo ok && sudo -S -p '' whoami"
    assert sudo_stdin == "cached-pass\n"


def test_cached_sudo_password_isolated_by_session_key(monkeypatch):
    monkeypatch.delenv("SUDO_PASSWORD", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)

    monkeypatch.setenv("HERMES_SESSION_KEY", "session-a")
    terminal_tool._set_cached_sudo_password("alpha-pass")

    monkeypatch.setenv("HERMES_SESSION_KEY", "session-b")
    assert terminal_tool._get_cached_sudo_password() == ""

    monkeypatch.setenv("HERMES_SESSION_KEY", "session-a")
    assert terminal_tool._get_cached_sudo_password() == "alpha-pass"


def test_validate_workdir_allows_windows_drive_paths():
    assert terminal_tool._validate_workdir(r"C:\Users\Alice\project") is None
    assert terminal_tool._validate_workdir("C:/Users/Alice/project") is None


def test_validate_workdir_allows_windows_unc_paths():
    assert terminal_tool._validate_workdir(r"\\server\share\project") is None


def test_validate_workdir_blocks_shell_metacharacters_in_windows_paths():
    assert terminal_tool._validate_workdir(r"C:\Users\Alice\project; rm -rf /")
    assert terminal_tool._validate_workdir(r"C:\Users\Alice\project$(whoami)")
    assert terminal_tool._validate_workdir("C:\\Users\\Alice\\project\nwhoami")


def test_gateway_session_env_is_available_to_foreground_commands():
    mock_env = MagicMock()
    mock_env.env = {"HERMES_SESSION_THREAD_ID": "stale-thread"}
    mock_env.execute.return_value = {"output": "done", "returncode": 0}
    tokens = set_session_vars(
        platform="discord",
        chat_id="channel-123",
        thread_id="thread-456",
        user_id="user-789",
    )
    try:
        with patch("tools.terminal_tool._get_env_config", return_value=_make_env_config()), \
             patch("tools.terminal_tool._start_cleanup_thread"), \
             patch("tools.terminal_tool._active_environments", {"default": mock_env}), \
             patch("tools.terminal_tool._last_activity", {"default": 0}), \
             patch("tools.terminal_tool._check_all_guards", return_value={"approved": True}):
            result = json.loads(terminal_tool.terminal_tool(command="echo ok"))
    finally:
        clear_session_vars(tokens)

    assert result["exit_code"] == 0
    assert mock_env.env["HERMES_SESSION_PLATFORM"] == "discord"
    assert mock_env.env["HERMES_SESSION_CHAT_ID"] == "channel-123"
    assert mock_env.env["HERMES_SESSION_THREAD_ID"] == "thread-456"
    assert mock_env.env["HERMES_SESSION_USER_ID"] == "user-789"


def test_gateway_session_env_is_cleared_when_context_is_empty():
    mock_env = MagicMock()
    mock_env.env = {
        "HERMES_SESSION_PLATFORM": "discord",
        "HERMES_SESSION_CHAT_ID": "channel-123",
        "HERMES_SESSION_THREAD_ID": "thread-456",
    }
    mock_env.execute.return_value = {"output": "done", "returncode": 0}

    with patch("tools.terminal_tool._get_env_config", return_value=_make_env_config()), \
         patch("tools.terminal_tool._start_cleanup_thread"), \
         patch("tools.terminal_tool._active_environments", {"default": mock_env}), \
         patch("tools.terminal_tool._last_activity", {"default": 0}), \
         patch("tools.terminal_tool._check_all_guards", return_value={"approved": True}):
        result = json.loads(terminal_tool.terminal_tool(command="echo ok"))

    assert result["exit_code"] == 0
    assert "HERMES_SESSION_PLATFORM" not in mock_env.env
    assert "HERMES_SESSION_CHAT_ID" not in mock_env.env
    assert "HERMES_SESSION_THREAD_ID" not in mock_env.env


def test_gateway_session_env_is_passed_to_background_process_registry():
    mock_env = MagicMock()
    mock_env.env = {}
    mock_proc_session = MagicMock()
    mock_proc_session.id = "proc-test"
    mock_proc_session.pid = 1234
    mock_registry = MagicMock()
    mock_registry.spawn_local.return_value = mock_proc_session
    tokens = set_session_vars(
        platform="discord",
        chat_id="channel-123",
        thread_id="thread-456",
    )
    try:
        with patch("tools.terminal_tool._get_env_config", return_value=_make_env_config()), \
             patch("tools.terminal_tool._start_cleanup_thread"), \
             patch("tools.terminal_tool._active_environments", {"default": mock_env}), \
             patch("tools.terminal_tool._last_activity", {"default": 0}), \
             patch("tools.terminal_tool._check_all_guards", return_value={"approved": True}), \
             patch("tools.process_registry.process_registry", mock_registry), \
             patch("tools.approval.get_current_session_key", return_value="session-key"):
            result = json.loads(
                terminal_tool.terminal_tool(command="sleep 10", background=True)
            )
    finally:
        clear_session_vars(tokens)

    assert result["session_id"] == "proc-test"
    env_vars = mock_registry.spawn_local.call_args.kwargs["env_vars"]
    assert env_vars["HERMES_SESSION_PLATFORM"] == "discord"
    assert env_vars["HERMES_SESSION_CHAT_ID"] == "channel-123"
    assert env_vars["HERMES_SESSION_THREAD_ID"] == "thread-456"
