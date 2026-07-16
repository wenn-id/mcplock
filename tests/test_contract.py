import json
import math
import unittest

from mcplock.contract import ContractError, build_lock, canonicalize, serialize_lock


class ContractArtifactTests(unittest.TestCase):
    def test_canonicalize_sorts_only_semantic_sets(self):
        value = {
            "z": 1,
            "required": ["beta", "alpha"],
            "type": ["string", "null"],
            "enum": ["z", "a", None],
            "oneOf": [{"type": "string"}, {"type": "integer"}],
        }
        result = canonicalize(value)
        self.assertEqual(list(result), ["enum", "oneOf", "required", "type", "z"])
        self.assertEqual(result["required"], ["alpha", "beta"])
        self.assertEqual(result["type"], ["null", "string"])
        self.assertEqual(result["enum"], ["a", "z", None])
        self.assertEqual(
            result["oneOf"],
            [{"type": "string"}, {"type": "integer"}],
        )

    def test_build_lock_sorts_tools_and_computes_stats(self):
        tools = [
            {"name": "zeta", "inputSchema": {"type": "object"}},
            {
                "name": "alpha",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        ]
        lock = build_lock(
            "2025-11-25",
            {"name": "fixture", "version": "1.0.0", "title": "ignored"},
            tools,
        )
        self.assertEqual(lock["lockVersion"], 1)
        self.assertEqual(set(lock), {
            "lockVersion", "protocolVersion", "server", "stats", "tools",
        })
        self.assertEqual(lock["server"], {"name": "fixture", "version": "1.0.0"})
        self.assertEqual([item["name"] for item in lock["tools"]], ["alpha", "zeta"])
        compact = json.dumps(
            lock["tools"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(lock["stats"], {
            "definitionBytes": len(compact),
            "toolCount": 2,
        })

    def test_serialization_is_byte_identical_with_one_final_newline(self):
        lock = build_lock(
            "2025-11-25",
            {"name": "fixture", "version": "1.0.0"},
            [{"name": "ping", "inputSchema": {"type": "object"}}],
        )
        self.assertEqual(serialize_lock(lock), serialize_lock(lock))
        self.assertTrue(serialize_lock(lock).endswith(b"\n"))
        self.assertFalse(serialize_lock(lock).endswith(b"\n\n"))

    def test_invalid_tools_duplicate_names_and_nan_are_rejected(self):
        invalid = [
            {},
            {"name": "", "inputSchema": {}},
            {"name": "x"},
            {"name": "x", "inputSchema": None},
        ]
        for tool in invalid:
            with self.subTest(tool=tool), self.assertRaises(ContractError):
                build_lock(
                    "2025-11-25",
                    {"name": "fixture", "version": "1.0.0"},
                    [tool],
                )
        duplicate = {"name": "same", "inputSchema": {"type": "object"}}
        with self.assertRaisesRegex(ContractError, "duplicate tool name"):
            build_lock(
                "2025-11-25",
                {"name": "fixture", "version": "1.0.0"},
                [duplicate, duplicate],
            )
        with self.assertRaisesRegex(ContractError, "finite"):
            build_lock(
                "2025-11-25",
                {"name": "fixture", "version": "1.0.0"},
                [{"name": "bad", "inputSchema": {"maximum": math.inf}}],
            )


if __name__ == "__main__":
    unittest.main()
