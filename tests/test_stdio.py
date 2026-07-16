import asyncio
from pathlib import Path
import sys
import unittest

from mcplock.stdio import Discovery, SUPPORTED_PROTOCOL_VERSIONS, discover

FAKE_SERVER = Path(__file__).with_name("fake_server.py")


def command(scenario):
    return [sys.executable, str(FAKE_SERVER), "--scenario", scenario]


class DiscoveryTests(unittest.TestCase):
    def test_supported_versions_are_exact(self):
        self.assertEqual(SUPPORTED_PROTOCOL_VERSIONS, {
            "2025-11-25",
            "2025-06-18",
            "2025-03-26",
            "2024-11-05",
        })

    def test_discovers_one_tool(self):
        result = asyncio.run(discover(command("baseline"), timeout=2.0))
        self.assertIsInstance(result, Discovery)
        self.assertEqual(result.protocol_version, "2025-11-25")
        self.assertEqual(result.server, {"name": "fixture", "version": "1.0.0"})
        self.assertEqual([item["name"] for item in result.tools], ["read_file"])

    def test_follows_every_page(self):
        result = asyncio.run(discover(command("pagination"), timeout=2.0))
        self.assertEqual([item["name"] for item in result.tools], ["alpha", "zeta"])

    def test_accepts_oldest_supported_revision(self):
        result = asyncio.run(discover(command("legacy"), timeout=2.0))
        self.assertEqual(result.protocol_version, "2024-11-05")


if __name__ == "__main__":
    unittest.main()
