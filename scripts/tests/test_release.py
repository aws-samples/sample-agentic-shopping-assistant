import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release import changed_version, version


class ReleaseTests(unittest.TestCase):
    def test_unreleased_baseline_does_not_tag(self):
        self.assertIsNone(version("# CHANGELOG\n## [Unreleased]\n- baseline"))

    def test_first_explicit_release_tags(self):
        self.assertEqual(changed_version("## [1.0.0] - 2026-09-15", "## [Unreleased]"), "v1.0.0")

    def test_unreleased_edits_do_not_retag_old_release(self):
        self.assertIsNone(changed_version("## [Unreleased]\nnew notes\n## [1.0.0]", "## [Unreleased]\n## [1.0.0]"))

    def test_new_release_heading_tags(self):
        self.assertEqual(changed_version("## [v1.1.0]\n## [1.0.0]", "## [1.0.0]"), "v1.1.0")
