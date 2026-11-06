import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import urllib.error

FILES = Path(__file__).resolve().parents[1] / "braintrust/files"
# The chart mounts both files together under these import names.
for name, file in [("brainstore_gate", "brainstore-startup-gate.py"), ("migration_gate", "migration-gate.py"), ("migration_rollback", "migration-rollback.py")]:
    spec = importlib.util.spec_from_file_location(name, FILES / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
gate = sys.modules["migration_gate"]


def job(*conditions, **metadata):
    return {"metadata": metadata, "status": {"conditions": [
        {"type": c, "status": "True"} for c in conditions
    ]}}


class JobGateTests(unittest.TestCase):
    def wait(self, observations, timeout=20):
        now = [0]
        def sleep(seconds):
            now[0] += seconds
        client = Mock(namespace="test-namespace")
        client.get.side_effect = observations
        gate.wait_for_job(client, "release-migrate-r2", timeout, 5,
                          clock=lambda: now[0], sleep=sleep, log=Mock())
        return client, now[0]

    def test_only_complete_condition_counts_as_success(self):
        for resource in ({}, job(), job("SuccessCriteriaMet"),
                         {"status": {"succeeded": 1}},
                         {"status": {"conditions": [{"type": "Complete", "status": "False"}]}},
                         job("Complete", deletionTimestamp="now")):
            with self.subTest(resource=resource):
                self.assertFalse(gate.job_complete(resource))
        self.assertTrue(gate.job_complete(job("Complete")))

    def test_failed_job_is_terminal_even_with_complete_condition(self):
        for condition in ("Failed", "FailureTarget"):
            with self.assertRaisesRegex(RuntimeError, "migration Job failed"):
                gate.job_complete(job(condition, "Complete"))

    def test_waits_for_exact_revision_and_retries_missing_job_and_rbac(self):
        observations = [urllib.error.HTTPError("url", code, "error", {}, None)
                        for code in (404, 403, 500)]
        client, elapsed = self.wait(observations + [job("Complete")])
        self.assertEqual(elapsed, 15)
        self.assertEqual(client.get.call_count, 4)
        for call in client.get.call_args_list:
            self.assertEqual(call.args, (
                "/apis/batch/v1/namespaces/test-namespace/jobs/release-migrate-r2", 20))

    def test_running_job_then_success(self):
        _, elapsed = self.wait([job(), job("Complete")])
        self.assertEqual(elapsed, 5)

    def test_timeout_never_allows_app_to_start(self):
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            self.wait([job()] * 4)

    def test_failed_job_never_allows_app_to_start(self):
        with self.assertRaisesRegex(RuntimeError, "migration Job failed"):
            self.wait([job("Failed")])

    def test_transient_observation_errors_retry(self):
        self.wait([OSError("network"), ValueError("bad JSON"), job("Complete")])

    def test_late_success_does_not_escape_deadline(self):
        now = [0]
        client = Mock(namespace="ns")
        def late(*args):
            now[0] = 21
            return job("Complete")
        client.get.side_effect = late
        with self.assertRaises(TimeoutError):
            gate.wait_for_job(client, "job", 20, 5, clock=lambda: now[0], sleep=Mock(), log=Mock())


class RollbackCleanupTests(unittest.TestCase):
    def test_cleanup_only_target_revision_and_waits_for_job_deletion(self):
        rollback = sys.modules["migration_rollback"]
        paths = rollback.resource_paths("ns", "target-r7")
        client = Mock()
        # DELETE succeeds; first GET still sees foreground deletion; next is gone.
        client.request.side_effect = [True, True, False] + [True, False] * 4
        now = [0]
        def sleep(seconds):
            now[0] += seconds
        rollback.cleanup(client, "ns", "target-r7", 10, clock=lambda: now[0], sleep=sleep)
        calls = client.request.call_args_list
        self.assertEqual([c.args[1] for c in calls if c.args[0] == "DELETE"], paths)
        self.assertEqual(calls[0].args, ("DELETE", paths[0], 10))
        self.assertEqual(calls[1].args, ("GET", paths[0], 10))
        self.assertEqual(calls[2].args, ("GET", paths[0], 10))
        self.assertEqual(now[0], 2)

    def test_cleanup_of_absent_resources_is_idempotent(self):
        rollback = sys.modules["migration_rollback"]
        client = Mock()
        client.request.return_value = False
        rollback.cleanup(client, "ns", "target-r7", 10, clock=lambda: 0, sleep=Mock())
        self.assertEqual(client.request.call_count, 10)

    def test_cleanup_fails_closed_on_stuck_deletion(self):
        rollback = sys.modules["migration_rollback"]
        client = Mock()
        client.request.return_value = True
        now = [0]
        def sleep(seconds):
            now[0] += seconds
        with self.assertRaises(TimeoutError):
            rollback.cleanup(client, "ns", "target-r7", 3, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(now[0], 3)

    def test_cleanup_permission_errors_stop_rollback(self):
        client = Mock()
        client.request.side_effect = RuntimeError("forbidden")
        with self.assertRaises(RuntimeError):
            sys.modules["migration_rollback"].cleanup(client, "ns", "target-r7", 10)


if __name__ == "__main__":
    unittest.main()
