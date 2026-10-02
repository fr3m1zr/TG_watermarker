"""Regression tests for non-secret container resource configuration."""

from __future__ import annotations

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
