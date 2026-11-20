"""Tests for scripts/check_docs.py, run against a scratch tree (no git, so the checker walks it)."""

from __future__ import annotations

import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", SCRIPT)
check_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_docs)

GUIDE = """# Operations

[한국어](operations.ko.md)

## Signals

| Event | Meaning |
| --- | --- |
| `tool_denied` | A denial |

```sh
echo hi
```

### Triage

See [triage](#triage) and [testing](testing.md#live-checks).
"""

GUIDE_KO = """# 운영

[English](operations.md)

## 관측 지표

| 이벤트 | 의미 |
| --- | --- |
| `tool_denied` | 거부 |

```sh
echo hi
```

### 초기 분석

[초기 분석](#초기-분석)과 [테스트](testing.ko.md#live-checks)를 참고하세요.
"""

TESTING = "# Testing\n\n[한국어](testing.ko.md)\n\n## Live checks\n"
TESTING_KO = '# 테스트\n\n[English](testing.md)\n\n## 실제 환경 검사\n\n<a id="live-checks"></a>\n'


class CheckDocsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved_root = check_docs.ROOT
        check_docs.ROOT = self.root
        self.write("docs/guides/operations.md", GUIDE)
        self.write("docs/guides/operations.ko.md", GUIDE_KO)
        self.write("docs/guides/testing.md", TESTING)
        self.write("docs/guides/testing.ko.md", TESTING_KO)

    def tearDown(self) -> None:
        check_docs.ROOT = self._saved_root
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, name: str, text: str) -> None:
        self.write(name, (self.root / name).read_text(encoding="utf-8") + text)

    def links(self) -> list:
        repo = check_docs.Repository()
        return check_docs.check_links(repo) + check_docs.check_labels(repo)

    def twins(self) -> list:
        return check_docs.check_twins(check_docs.Repository())

    def test_a_clean_tree_passes(self) -> None:
        self.assertEqual(self.links(), [])
        self.assertEqual(self.twins(), [])

    def test_a_missing_anchor_fails(self) -> None:
        self.append("docs/guides/operations.md", "\n[gone](testing.md#nowhere)\n")
        self.assertEqual(len(self.links()), 1)
        self.assertIn("no heading or id #nowhere", self.links()[0])

    def test_a_link_to_its_own_file_needs_an_anchor(self) -> None:
        self.append("docs/guides/operations.md", "\nSee [operations](operations.md).\n")
        failures = self.links()
        self.assertEqual(len(failures), 1)
        self.assertIn("links to its own file", failures[0])

    def test_a_link_to_its_own_file_with_an_anchor_passes(self) -> None:
        self.append("docs/guides/operations.md", "\nSee [signals](operations.md#signals).\n")
        self.assertEqual(self.links(), [])

    def test_a_label_naming_another_file_fails(self) -> None:
        self.append("docs/guides/operations.md", "\nSee [scripts/README.md](testing.md).\n")
        failures = self.links()
        self.assertEqual(len(failures), 1)
        self.assertIn("names one file and links another", failures[0])

    def test_a_label_naming_the_english_file_may_link_the_korean_twin(self) -> None:
        self.append("docs/guides/operations.ko.md", "\n[`testing.md`](testing.ko.md)\n")
        self.assertEqual(self.links(), [])

    def test_labels_inside_fenced_code_are_ignored(self) -> None:
        self.append("docs/guides/operations.md", "\n```md\n[scripts/README.md](testing.md)\n```\n")
        self.append("docs/guides/operations.ko.md", "\n```md\n[scripts/README.md](testing.md)\n```\n")
        self.assertEqual(self.links(), [])

    def test_twins_need_the_same_headings(self) -> None:
        self.append("docs/guides/operations.md", "\n## Rotate credentials\n")
        failures = self.twins()
        self.assertEqual(len(failures), 1)
        self.assertIn("differ in headings", failures[0])

    def test_twins_need_the_same_table_rows(self) -> None:
        self.append("docs/guides/operations.md", "\n| `tool_result_blocked` | A block |\n")
        failures = self.twins()
        self.assertEqual(len(failures), 1)
        self.assertIn("table rows", failures[0])

    def test_twins_need_the_same_fenced_blocks(self) -> None:
        self.append("docs/guides/operations.md", "\n```sh\nexit 0\n```\n")
        failures = self.twins()
        self.assertEqual(len(failures), 1)
        self.assertIn("fenced code blocks", failures[0])

    def test_a_guide_without_a_twin_fails(self) -> None:
        self.write("docs/guides/release.md", "# Release\n")
        self.assertIn("docs/guides/release.md has no Korean twin docs/guides/release.ko.md", self.twins())

    def test_slugs_follow_github_and_gitlab(self) -> None:
        self.assertEqual(check_docs.github_slug("Memory and PII"), "memory-and-pii")
        self.assertEqual(check_docs.github_slug("`ALLOWED_TOOLS` — the list"), "allowed_tools--the-list")
        self.assertEqual(check_docs.gitlab_slug("`ALLOWED_TOOLS` — the list"), "allowed_tools-the-list")
        self.assertEqual(check_docs.github_slug("Memory와 개인 식별 정보"), "memory와-개인-식별-정보")


if __name__ == "__main__":
    unittest.main()


class GeneratedDocsCommandTests(unittest.TestCase):
    def test_the_message_names_the_command_check_generated_runs(self):
        # The call spells its arguments out as literals; GEN_DOCS, which its messages show, must match them.
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        (function,) = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "check_generated"]
        (call,) = [node for node in ast.walk(function) if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "run"]
        arguments = ast.literal_eval(call.args[0])
        self.assertEqual(arguments, check_docs.GEN_DOCS.split())
        self.assertFalse(any(keyword.arg == "shell" for keyword in call.keywords))
