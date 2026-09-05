"""Packaging and version-identity contract for MCPLock."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tomllib
import unittest

import mcplock
from mcplock.cli import main
from mcplock.stdio import CLIENT_INFO

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "mcplock"


def metadata():
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


class VersionIdentityTests(unittest.TestCase):
    def test_package_exposes_its_version(self):
        self.assertRegex(mcplock.__version__, r"^\d+\.\d+\.\d+")

    def test_declared_version_has_a_single_source(self):
        self.assertEqual(metadata()["version"], mcplock.__version__)

    def test_handshake_reports_the_package_version(self):
        self.assertEqual(CLIENT_INFO["name"], "mcplock")
        self.assertEqual(CLIENT_INFO["version"], mcplock.__version__)

    def test_version_flag_prints_the_package_version(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as caught:
                main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"mcplock {mcplock.__version__}")


class PackagingMetadataTests(unittest.TestCase):
    def test_project_urls_point_at_the_repository(self):
        urls = metadata()["urls"]
        for key in ("Homepage", "Repository", "Issues"):
            self.assertIn("github.com/wenn-id/mcplock", urls[key], key)

    def test_license_file_is_declared_for_distributions(self):
        self.assertIn("LICENSE", metadata()["license-files"])
        self.assertTrue((REPO_ROOT / "LICENSE").is_file())

    def test_inline_type_information_is_advertised(self):
        marker = PACKAGE_ROOT / "py.typed"
        self.assertTrue(marker.is_file(), "src/mcplock/py.typed must exist")
        self.assertEqual(marker.read_bytes(), b"")

    def test_topic_classifiers_describe_the_tool(self):
        topics = [
            item
            for item in metadata()["classifiers"]
            if item.startswith("Topic ::")
        ]
        self.assertIn("Topic :: Software Development :: Quality Assurance", topics)
        self.assertIn("Topic :: Software Development :: Testing", topics)


if __name__ == "__main__":
    unittest.main()
