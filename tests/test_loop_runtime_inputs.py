import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LoopRuntimeInputTests(unittest.TestCase):
    def render(self, **values):
        args = ["helm", "template", "test", str(ROOT / "braintrust"),
                "-f", str(ROOT / "braintrust/tests/__fixtures__/base-values.yaml"),
                "-f", str(ROOT / "braintrust/tests/__fixtures__/loop-runtime-values.yaml"),
                "--show-only", "templates/loop-runtime-configmap.yaml"]
        for field, value in values.items():
            args.extend(["--set-json", f"loopRuntime.{field}={json.dumps(value)}"])
        return subprocess.run(args, capture_output=True, text=True)

    def test_invalid_numbers(self):
        for field in ("service.port", "sandbox.vcpuCount", "sandbox.memoryMib"):
            invalid = [None, "", 0, -1, 1.5, True, [], {}, "oops", "1.5"]
            if field == "service.port":
                invalid += [65536, "65536", "999999999999999999999", "01"]
            for value in invalid:
                with self.subTest(field=field, value=value):
                    result = self.render(**{field: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"loopRuntime.{field} must be", result.stderr)

    def test_valid_numbers(self):
        for field, env, values in (
            ("service.port", "LOOP_RUNTIME_PORT", [1, 65535, "4001"]),
            ("sandbox.vcpuCount", "EXO_SANDBOX_VCPU_COUNT", [1, "2", "01"]),
            ("sandbox.memoryMib", "EXO_SANDBOX_MEMORY_MIB", [1, "2048", "01"]),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    result = self.render(**{field: value})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f'{env}: "{value}"', result.stdout)

    def test_required_strings(self):
        for field in ("serviceAccount.googleServiceAccount", "sandbox.dnsUrl", "sandbox.image",
                      "sandbox.egressGateway.address", "sandbox.egressGateway.url", "image.repository", "image.tag"):
            for value in (None, "", " \t\n", " padded "):
                with self.subTest(field=field, value=value):
                    result = self.render(**{field: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"loopRuntime.{field}", result.stderr)

    def test_missing_nested_defaults(self):
        for field in ("name", "image.pullPolicy", "service.type", "service.portName", "serviceAccount.name",
                      "labels", "podLabels"):
            for value in (None, ""):
                with self.subTest(field=field, value=value):
                    result = self.render(**{field: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("upgrade with --reset-then-reuse-values", result.stderr)

    def test_image_tag_formats(self):
        for tag in ("v2.21.0", "2.21.0", "a" * 40, "deadbeef", "1234567", 1234567,
                    "v2.21.0-rc.1", "custom-tag"):
            with self.subTest(tag=tag):
                result = self.render(**{"image.tag": tag})
                self.assertEqual(result.returncode, 0, result.stderr)
