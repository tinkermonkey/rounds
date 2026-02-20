"""Tests for interactive CLI loop functionality.

Covers:
- _run_cli_interactive: REPL-like command loop
- EOF/KeyboardInterrupt handling
- JSON command parsing
"""

import json
from unittest.mock import patch

import pytest

from rounds.adapters.cli.commands import CLICommandHandler
from rounds.main import _run_cli_interactive
from rounds.tests.fakes.management import FakeManagementPort


@pytest.mark.asyncio
class TestInteractiveCLILoop:
    """Test suite for interactive CLI loop."""

    async def test_cli_reads_and_executes_commands(self) -> None:
        """Test that CLI reads commands in 'command args_json' format and executes them."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            "list {}",
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            # Should complete without error
            await _run_cli_interactive(handler)

    async def test_cli_handles_json_parse_errors(self) -> None:
        """Test that CLI handles malformed JSON gracefully."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            "list not-valid-json",
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            # Should handle error and continue
            await _run_cli_interactive(handler)

    async def test_cli_handles_eof(self) -> None:
        """Test that CLI handles EOF (Ctrl+D) gracefully."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        def input_with_eof(_: str) -> str:
            raise EOFError()

        with patch("builtins.input", side_effect=input_with_eof):
            # Should exit gracefully without exception
            await _run_cli_interactive(handler)

    async def test_cli_handles_keyboard_interrupt(self) -> None:
        """Test that CLI handles KeyboardInterrupt (Ctrl+C) gracefully."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        # Create a mock that raises once, then returns exit
        call_count = [0]

        def input_with_interrupt(_: str) -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyboardInterrupt()
            return "exit"

        with patch("builtins.input", side_effect=input_with_interrupt):
            # Should handle interrupt and continue, then exit gracefully
            await _run_cli_interactive(handler)

    async def test_cli_executes_mute_command(self) -> None:
        """Test that CLI correctly executes mute command."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            f'mute {json.dumps({"signature_id": "sig-123", "reason": "false positive"})}',
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            with patch("builtins.print"):
                await _run_cli_interactive(handler)

        # Verify mute was called
        assert "sig-123" in management.muted_signatures
        assert management.muted_signatures["sig-123"] == "false positive"

    async def test_cli_executes_resolve_command(self) -> None:
        """Test that CLI correctly executes resolve command."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            f'resolve {json.dumps({"signature_id": "sig-456", "fix_applied": "patched"})}',
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            with patch("builtins.print"):
                await _run_cli_interactive(handler)

        # Verify resolve was called
        assert "sig-456" in management.resolved_signatures
        assert management.resolved_signatures["sig-456"] == "patched"

    async def test_cli_ignores_empty_input(self) -> None:
        """Test that CLI ignores empty input lines."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            "",  # Empty input
            "   ",  # Whitespace only
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            # Should complete without error
            await _run_cli_interactive(handler)

    async def test_cli_shows_help_command(self) -> None:
        """Test that CLI shows help on 'help' command."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            "help",
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            with patch("builtins.print") as mock_print:
                await _run_cli_interactive(handler)

            # Should have printed help
            help_calls = [call for call in mock_print.call_args_list
                         if any("Available Commands" in str(arg) for arg in call[0])]
            assert len(help_calls) > 0

    async def test_cli_handles_invalid_command(self) -> None:
        """Test that CLI handles unknown commands gracefully."""
        management = FakeManagementPort()
        handler = CLICommandHandler(management)

        commands = [
            "invalid_command {}",
            "exit",
        ]

        with patch("builtins.input", side_effect=commands):
            with patch("builtins.print") as mock_print:
                await _run_cli_interactive(handler)

            # Should have printed error
            error_calls = [call for call in mock_print.call_args_list
                          if any("error" in str(arg).lower() for arg in call[0])]
            assert len(error_calls) > 0
