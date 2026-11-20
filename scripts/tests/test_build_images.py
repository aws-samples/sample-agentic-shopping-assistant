import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_images
from build_images import container_engine, run_engine


class ContainerEngineTests(unittest.TestCase):
    def test_defaults_to_docker(self):
        with mock.patch("build_images.shutil.which", return_value="/usr/local/bin/docker") as which:
            self.assertEqual(container_engine({}), "docker")
            which.assert_called_once_with("docker")

    def test_selects_known_engines_by_name_or_path(self):
        with mock.patch("build_images.shutil.which", return_value="/resolved"):
            self.assertEqual(container_engine({"CONTAINER_ENGINE": "finch"}), "finch")
            self.assertEqual(container_engine({"CDK_DOCKER": "/opt/homebrew/bin/podman"}), "podman")

    def test_rejects_unknown_executables(self):
        for value in ("bash", "/opt/untrusted/docker-evil", "docker; rm -rf /", "sh -c id"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                container_engine({"CONTAINER_ENGINE": value})

    def test_rejects_engines_missing_from_path(self):
        with mock.patch("build_images.shutil.which", return_value=None), self.assertRaises(SystemExit):
            container_engine({"CONTAINER_ENGINE": "podman"})


class RunEngineTests(unittest.TestCase):
    def test_runs_a_literal_program_with_list_args_and_no_shell(self):
        for engine in ("docker", "finch", "podman"):
            with self.subTest(engine=engine), mock.patch.object(build_images.subprocess, "run") as run:
                run_engine(engine, ["image", "inspect", "aria-api:abc"], capture_output=True)
                run.assert_called_once_with([engine, "image", "inspect", "aria-api:abc"], check=True, capture_output=True)

    def test_refuses_anything_else(self):
        with mock.patch.object(build_images.subprocess, "run") as run, self.assertRaises(SystemExit):
            run_engine("/opt/untrusted/docker", ["build", "."])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
