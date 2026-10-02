"""Regression tests for non-secret container resource configuration."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StartupConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.startup = (PROJECT_ROOT / "startup.sh").read_text()
        cls.compose = (PROJECT_ROOT / "compose.yaml").read_text()
        cls.env_example = (PROJECT_ROOT / ".env.example").read_text()

    def test_apple_startup_passes_configured_bot_memory_to_create(self) -> None:
        self.assertIn(
            'bot_memory_limit=$(env_or_default BOT_MEMORY_LIMIT 2G)',
            self.startup,
        )
        self.assertIn('--memory "$bot_memory_limit"', self.startup)
        self.assertRegex(
            self.startup,
            r'"\$max_image_pixels" \\\n\s+"\$bot_memory_limit" \\\n\s+"\$transfer_timeout"',
        )

    def test_compose_and_env_example_expose_same_memory_default(self) -> None:
        self.assertIn("BOT_MEMORY_LIMIT=2G", self.env_example)
        self.assertIn("mem_limit: ${BOT_MEMORY_LIMIT:-2G}", self.compose)

    def test_existing_file_mode_and_transfer_defaults_remain_unchanged(self) -> None:
        self.assertIn("MAX_FILE_SIZE_MB=100", self.env_example)
        self.assertIn(
            'max_file_size=$(env_or_default MAX_FILE_SIZE_MB 100)',
            self.startup,
        )
        self.assertIn('--env TELEGRAM_LOCAL_MODE=true', self.startup)
        self.assertIn(
            'transfer_timeout=$(env_or_default FILE_TRANSFER_TIMEOUT_SECONDS 300)',
            self.startup,
        )


if __name__ == "__main__":
    unittest.main()


class DockerRuntimeTests(unittest.TestCase):
    """Run startup.sh against a fake docker binary to check the Linux path."""

    def _run(self, *args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            calls = tmp_path / "calls.log"
            fake = tmp_path / "docker"
            fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n')
            fake.chmod(0o755)
            env_file = tmp_path / ".env"
            env_file.write_text("TELEGRAM_BOT_TOKEN=test\n")
            env = {
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "TG_WATERMARKER_RUNTIME": "docker",
                "ENV_FILE": str(env_file),
            }
            result = subprocess.run(
                ["sh", str(PROJECT_ROOT / "startup.sh"), *args],
                env=env, capture_output=True, text=True, check=False,
            )
            lines = calls.read_text().splitlines() if calls.exists() else []
            return result, lines

    def test_start_runs_compose_up_detached(self) -> None:
        result, calls = self._run("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("compose --project-directory "))
        self.assertTrue(calls[0].endswith(" up -d"))

    def test_logs_maps_service_aliases(self) -> None:
        result, calls = self._run("logs", "api", "-f")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(calls[0].endswith("logs --tail 200 --follow telegram-bot-api"))

    def test_supervisor_is_rejected_on_docker(self) -> None:
        result, calls = self._run("supervisor", "status")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
