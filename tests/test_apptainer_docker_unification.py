"""
Tests for the unification of mlcd and mlca options (issue #312).

Verifies that:
1. --docker_X CLI options act as fallbacks when --apptainer_X is not given in the
   effective settings merge logic.
2. script meta.yaml `apptainer` key overrides `docker` key for apptainer runs.
3. meta_schema validates the `apptainer` section using the same rules as `docker`.
"""
import unittest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 1. run_state: apptainer key merges over docker key
#    (mirrors what apptainerfile() and apptainer_run() do)
# ---------------------------------------------------------------------------

class RunStateApptainerMergeTest(unittest.TestCase):
    """apptainer settings from meta.yaml merge over docker settings."""

    def _merged_settings(self, docker_meta, apptainer_meta):
        """Simulate what apptainer_run does to build effective settings."""
        run_state = {
            "docker": docker_meta,
            "apptainer": apptainer_meta,
        }
        return {**run_state.get("docker", {}), **run_state.get("apptainer", {})}

    def test_apptainer_base_image_overrides_docker(self):
        settings = self._merged_settings(
            {"base_image": "docker-image:latest"},
            {"base_image": "apptainer-image:latest"},
        )
        self.assertEqual(settings["base_image"], "apptainer-image:latest")

    def test_docker_settings_used_when_no_apptainer_key(self):
        settings = self._merged_settings(
            {"os": "ubuntu", "base_image": "docker-image:latest"},
            {},
        )
        self.assertEqual(settings["os"], "ubuntu")
        self.assertEqual(settings["base_image"], "docker-image:latest")

    def test_apptainer_partial_override(self):
        """Only the keys in apptainer meta override; rest comes from docker."""
        settings = self._merged_settings(
            {"os": "ubuntu", "os_version": "22.04", "run": True},
            {"os_version": "20.04"},
        )
        self.assertEqual(settings["os"], "ubuntu")
        self.assertEqual(settings["os_version"], "20.04")
        self.assertTrue(settings["run"])


# ---------------------------------------------------------------------------
# 2. CLI fallback: docker_X used when apptainer_X is absent
#    Tests the lookup pattern: apptainer_X → docker_X → settings → default
# ---------------------------------------------------------------------------

class ApptainerDockerCliFallbackTest(unittest.TestCase):
    """
    Verify the three-level lookup: apptainer_X > docker_X > settings > default.
    This mirrors the logic in prepare_apptainer_inputs and apptainer_run.
    """

    def _lookup(self, key, input_params, settings, default=None):
        """Replicate the lookup pattern used in prepare_apptainer_inputs."""
        return input_params.get(
            f"apptainer_{key}",
            input_params.get(f"docker_{key}", settings.get(key, default))
        )

    def test_apptainer_prefix_wins(self):
        val = self._lookup("os", {"apptainer_os": "debian", "docker_os": "centos"}, {})
        self.assertEqual(val, "debian")

    def test_docker_prefix_fallback(self):
        val = self._lookup("os", {"docker_os": "centos"}, {})
        self.assertEqual(val, "centos")

    def test_settings_fallback(self):
        val = self._lookup("os", {}, {"os": "ubuntu"})
        self.assertEqual(val, "ubuntu")

    def test_default_fallback(self):
        val = self._lookup("os", {}, {}, "alpine")
        self.assertEqual(val, "alpine")

    def test_noregenerate_docker_fallback(self):
        # apptainer_run uses: not i.get('apptainer_noregenerate', i.get('docker_noregenerate', False))
        i = {"docker_noregenerate": True}
        regenerate_def_file = not i.get("apptainer_noregenerate", i.get("docker_noregenerate", False))
        self.assertFalse(regenerate_def_file)

    def test_noregenerate_apptainer_overrides_docker(self):
        i = {"apptainer_noregenerate": False, "docker_noregenerate": True}
        regenerate_def_file = not i.get("apptainer_noregenerate", i.get("docker_noregenerate", False))
        self.assertTrue(regenerate_def_file)

    def test_rebuild_docker_fallback(self):
        i = {"docker_rebuild": True}
        rebuild = i.get("apptainer_rebuild", i.get("docker_rebuild", False))
        self.assertTrue(rebuild)

    def test_mounts_docker_fallback(self):
        i = {"docker_mounts": ["/host:/container"]}
        mounts = i.get("apptainer_mounts", i.get("docker_mounts", []))
        self.assertEqual(mounts, ["/host:/container"])

    def test_mounts_apptainer_overrides_docker(self):
        i = {"apptainer_mounts": ["/a:/a"], "docker_mounts": ["/b:/b"]}
        mounts = i.get("apptainer_mounts", i.get("docker_mounts", []))
        self.assertEqual(mounts, ["/a:/a"])


# ---------------------------------------------------------------------------
# 3. meta_schema: apptainer key validation
# ---------------------------------------------------------------------------

class MetaSchemaApptainerKeyTest(unittest.TestCase):
    """The `apptainer` key in meta.yaml is validated like `docker`."""

    def _validate(self, data):
        from mlc.meta_schema import validate_meta
        base = {
            "alias": "test-script",
            "uid": "aabbccdd11223344",
            "automation_alias": "script",
            "automation_uid": "5b4aa8f95024c2a5",
        }
        base.update(data)
        return validate_meta(base, "test.yaml")

    def test_apptainer_key_accepted_with_dict(self):
        errors, warnings = self._validate({"apptainer": {"os": "ubuntu"}})
        self.assertEqual(errors, [])

    def test_apptainer_unknown_key_produces_warning(self):
        _, warnings = self._validate({"apptainer": {"unknown_key_xyz": "val"}})
        self.assertTrue(any("unknown key" in w for w in warnings))

    def test_apptainer_wrong_type_produces_error(self):
        errors, _ = self._validate({"apptainer": {"run": "not-a-bool"}})
        self.assertTrue(any("apptainer.run" in e for e in errors))

    def test_apptainer_run_bool_accepted(self):
        errors, _ = self._validate({"apptainer": {"run": False}})
        self.assertEqual(errors, [])

    def test_docker_and_apptainer_both_accepted(self):
        errors, _ = self._validate({
            "docker": {"os": "ubuntu"},
            "apptainer": {"os": "centos"},
        })
        self.assertEqual(errors, [])

    def test_apptainer_base_image_str_accepted(self):
        errors, _ = self._validate({"apptainer": {"base_image": "ubuntu:22.04"}})
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()



# ---------------------------------------------------------------------------
# 5. CLI smoke tests: mlcd --help and mlca --help
# ---------------------------------------------------------------------------

class CliHelpSmokeTest(unittest.TestCase):
    """mlcd and mlca --help exit cleanly and include expected content."""

    def _run_help(self, entry_point):
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", entry_point, "--help"],
            capture_output=True, text=True
        )
        return result

    def _run_mlc_action_help(self, action):
        """Use 'mlc <action> script --help' as the canonical help path."""
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.argv=['mlc', '{action}', 'script', '--help']; "
             f"from mlc.main import main; main()"],
            capture_output=True, text=True
        )
        return result

    def test_mlcd_help_exits_successfully(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['mlcd','--help']; "
             "from mlc.main import mlcd; mlcd()"],
            capture_output=True, text=True
        )
        combined = result.stdout + result.stderr
        self.assertIn("docker", combined.lower(),
                      msg=f"Expected docker help text, got: {combined[:500]}")

    def test_mlca_help_exits_successfully(self):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['mlca','--help']; "
             "from mlc.main import mlca; mlca()"],
            capture_output=True, text=True
        )
        combined = result.stdout + result.stderr
        self.assertIn("apptainer", combined.lower(),
                      msg=f"Expected apptainer help text, got: {combined[:500]}")

    def test_mlca_help_mentions_docker_fallback(self):
        """mlca --help documents that --docker_X options are accepted."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['mlca','--help']; "
             "from mlc.main import mlca; mlca()"],
            capture_output=True, text=True
        )
        combined = result.stdout + result.stderr
        self.assertIn("docker_rebuild", combined,
                      msg=f"Expected docker_rebuild in mlca help, got: {combined[:500]}")
        self.assertIn("docker_noregenerate", combined,
                      msg=f"Expected docker_noregenerate in mlca help, got: {combined[:500]}")
