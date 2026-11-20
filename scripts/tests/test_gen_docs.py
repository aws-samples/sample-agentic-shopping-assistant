"""gen_docs reads a local tool's @tool arguments from its source, and never runs the module."""
import ast
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gen_docs  # noqa: E402
from gen_docs import literal_value, module_literals  # noqa: E402


def literals(source: str) -> dict:
    return module_literals(ast.parse(textwrap.dedent(source)))


def value(expression: str, bound: dict | None = None):
    return literal_value(ast.parse(expression, mode="eval").body, bound or {})


class LiteralTests(unittest.TestCase):
    def test_module_literals_keep_names_bound_to_literals(self):
        found = literals("""
            LIMIT = 20
            NAME: str = "get_points"
            SCHEMA = {"type": "object", "properties": {"months": {"type": "integer", "maximum": LIMIT}}}
            LIMIT = 30
            CLIENT = make_client()
            import os
        """)
        self.assertEqual(found["NAME"], "get_points")
        # Read in order: SCHEMA saw the first LIMIT, and the later assignment wins for LIMIT itself.
        self.assertEqual(found["SCHEMA"]["properties"]["months"]["maximum"], 20)
        self.assertEqual(found["LIMIT"], 30)
        self.assertNotIn("CLIENT", found)

    def test_a_name_bound_to_something_else_drops_out(self):
        found = literals("""
            DESCRIPTION = "points"
            DESCRIPTION = describe()
        """)
        self.assertNotIn("DESCRIPTION", found)

    def test_values_are_literals_and_bound_names(self):
        self.assertEqual(value('{"json": SCHEMA, "n": [1, 2.5, None, True]}', {"SCHEMA": {"type": "object"}}),
                         {"json": {"type": "object"}, "n": [1, 2.5, None, True]})
        self.assertEqual(value('"one " "string"'), "one string")

    def test_anything_that_would_run_code_is_refused(self):
        for expression in ("build_schema()", 'f"{NAME}"', "UNKNOWN", "__import__('os').system('true')", "[x for x in ()]",
                           "lambda: 1", "SCHEMA['json']"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                value(expression, {"NAME": "n", "SCHEMA": {"json": {}}})


class LocalToolTests(unittest.TestCase):
    def catalog(self, source: str):
        """local_tools() over one tool module; returns the tools, and whether the module's code ran."""
        with tempfile.TemporaryDirectory() as root:
            tools, marker = Path(root) / "tools", Path(root) / "module-ran"
            tools.mkdir()
            (tools / "loyalty.py").write_text(textwrap.dedent(source).replace("MARKER_PATH", repr(str(marker))), encoding="utf-8")
            with mock.patch.object(gen_docs, "ROOT", Path(root)), mock.patch.object(gen_docs, "AGENT_TOOLS", tools), \
                    mock.patch.object(gen_docs, "search_tool", lambda: "search"), mock.patch.object(gen_docs, "link", str):
                return gen_docs.local_tools(), marker.exists()

    def test_a_tool_is_catalogued_without_running_its_module(self):
        found, ran = self.catalog("""
            import package_that_is_not_installed
            from pathlib import Path
            from strands import tool
            Path(MARKER_PATH).write_text("ran")
            DESCRIPTION = "The shopper's loyalty points balance"
            SCHEMA = {"type": "object", "properties": {"months": {"type": "integer"}}}

            @tool(name="get_points", description=DESCRIPTION, inputSchema={"json": SCHEMA})
            def get_points(months: int = 3) -> dict:
                return {}
        """)
        self.assertFalse(ran, "gen_docs ran the tool module's code")
        self.assertEqual(found[0], "search")
        (points,) = found[1:]
        self.assertEqual((points.name, points.description), ("get_points", "The shopper's loyalty points balance"))
        self.assertEqual(points.schema, {"type": "object", "properties": {"months": {"type": "integer"}}})

    def test_arguments_that_need_running_code_fail_with_the_reason(self):
        with self.assertRaisesRegex(SystemExit, "must be literals, or names bound to literals"):
            self.catalog("""
                from strands import tool

                @tool(name="get_points", description="Points", inputSchema={"json": build_schema()})
                def get_points() -> dict:
                    return {}
            """)


if __name__ == "__main__":
    unittest.main()
