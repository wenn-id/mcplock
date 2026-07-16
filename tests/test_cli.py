from contextlib import redirect_stderr, redirect_stdout
import gc
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

from mcplock.cli import main
from mcplock.contract import build_lock, write_lock

FAKE_SERVER = Path(__file__).with_name("fake_server.py")


def server(scenario):
    return [sys.executable, str(FAKE_SERVER), "--scenario", scenario]


class CliTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_update_then_compatible_check(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory) / "mcp.lock.json")
            code, output, error = self.invoke(
                ["update", "--lock", lock, "--", *server("baseline")]
            )
            self.assertEqual((code, error), (0, ""))
            self.assertIn("wrote", output)
            code, output, error = self.invoke(
                ["check", "--lock", lock, "--", *server("baseline")]
            )
            self.assertEqual((code, error), (0, ""))
            self.assertIn("compatible", output)

    def test_breaking_check_exits_one_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "mcp.lock.json"
            self.invoke(["update", "--lock", str(lock), "--", *server("baseline")])
            before = lock.read_bytes()
            code, output, error = self.invoke(
                ["check", "--lock", str(lock), "--", *server("breaking")]
            )
            self.assertEqual((code, error), (1, ""))
            self.assertIn("BREAKING", output)
            self.assertIn("input became required", output)
            self.assertEqual(lock.read_bytes(), before)

    def test_missing_lock_and_protocol_failure_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.json")
            code, _, error = self.invoke(
                ["check", "--lock", missing, "--", *server("baseline")]
            )
            self.assertEqual(code, 2)
            self.assertIn("mcplock update", error)
            code, _, error = self.invoke(
                ["check", "--lock", missing, "--", *server("stderr-exit")]
            )
            self.assertEqual(code, 2)
            self.assertIn("fixture launch failed", error)

    def test_failed_update_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "mcp.lock.json"
            lock.write_text("keep-me", encoding="utf-8")
            code, _, _ = self.invoke(
                ["update", "--lock", str(lock), "--", *server("malformed")]
            )
            self.assertEqual(code, 2)
            self.assertEqual(lock.read_text(encoding="utf-8"), "keep-me")

    def test_unsupported_lock_protocol_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "mcp.lock.json"
            write_lock(lock, build_lock(
                "2099-01-01",
                {"name": "fixture", "version": "1.0.0"},
                [{
                    "name": "read_file",
                    "inputSchema": {"type": "object"},
                }],
            ))
            code, _, error = self.invoke(
                ["check", "--lock", str(lock), "--", *server("baseline")]
            )
            self.assertEqual(code, 2)
            self.assertIn("unsupported lockfile protocol version", error)

    def test_warning_only_check_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = str(Path(directory) / "mcp.lock.json")
            self.invoke(["update", "--lock", lock, "--", *server("baseline")])
            with patch("mcplock.cli.compare_locks") as compare:
                from mcplock.contract import Change
                compare.return_value = [Change("warning", "server.version", "changed")]
                code, output, _ = self.invoke(
                    ["check", "--lock", lock, "--", *server("baseline")]
                )
            self.assertEqual(code, 0)
            self.assertIn("WARNING", output)

    def test_keyboard_interrupt_returns_130(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch("mcplock.cli.asyncio.run", side_effect=KeyboardInterrupt):
                code, _, _ = self.invoke(["update", "--", "fixture"])
            gc.collect()
        self.assertEqual(code, 130)
        self.assertEqual(caught, [])


if __name__ == "__main__":
    unittest.main()
