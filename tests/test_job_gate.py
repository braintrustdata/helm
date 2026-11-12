import importlib.util
import itertools
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error

FILES = Path(__file__).resolve().parents[1] / "braintrust/files"
# The chart mounts the shared runtime under these import names.
for name, file in [
    ("brainstore_gate", "brainstore-startup-gate.py"),
    ("job_gate", "job-gate.py"),
    ("migration_rollback", "migration-rollback.py"),
]:
    spec = importlib.util.spec_from_file_location(name, FILES / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
gate = sys.modules["job_gate"]
rollback = sys.modules["migration_rollback"]


def job(*conditions, **metadata):
    return {"metadata": metadata, "status": {"conditions": [
        {"type": condition, "status": "True"} for condition in conditions
    ]}}


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class JobGateTests(unittest.TestCase):
    def wait(self, observations, name="release-migrate-r2", timeout=20):
        clock = Clock()
        client = Mock(namespace="test-namespace")
        client.get.side_effect = observations
        gate.wait_for_job(client, name, timeout, 5,
                          clock=clock, sleep=clock.sleep, log=Mock())
        return client, clock.now

    def test_only_complete_condition_counts_as_success(self):
        for resource in ({}, job(), job("SuccessCriteriaMet"),
                         {"status": {"succeeded": 1}},
                         {"status": {"conditions": [{"type": "Complete", "status": "False"}]}},
                         job("Complete", deletionTimestamp="now")):
            with self.subTest(resource=resource):
                self.assertFalse(gate.job_complete(resource))
        self.assertTrue(gate.job_complete(job("Complete")))

    def test_failure_takes_precedence_over_completion_and_deletion(self):
        for condition in ("Failed", "FailureTarget"):
            for metadata in ({}, {"deletionTimestamp": "now"}):
                with self.subTest(condition=condition, metadata=metadata):
                    with self.assertRaises(RuntimeError):
                        gate.job_complete(job(condition, "Complete", **metadata))

    def test_exact_job_wait_retries_missing_job_rbac_and_server_errors(self):
        errors = [urllib.error.HTTPError("url", code, "error", {}, None)
                  for code in (404, 403, 500)]
        client, elapsed = self.wait(errors + [job("Complete")])
        self.assertEqual(elapsed, 15)
        self.assertEqual(
            {call.args for call in client.get.call_args_list},
            {("/apis/batch/v1/namespaces/test-namespace/jobs/release-migrate-r2", 20)},
        )

    def test_retained_success_does_not_wait_for_another_observation(self):
        client = Mock(namespace="ns")
        client.get.return_value = job("Complete")
        sleep = Mock()
        gate.wait_for_job(client, "release-migrate-r1", 20, 5, clock=lambda: 0,
                          sleep=sleep, log=Mock())
        self.assertEqual(client.get.call_count, 1)
        sleep.assert_not_called()

    def test_running_job_then_success(self):
        _, elapsed = self.wait([job(), job("Complete")])
        self.assertEqual(elapsed, 5)

    def test_running_missing_or_forbidden_job_never_allows_startup(self):
        for observation in (job(), urllib.error.HTTPError("url", 404, "", {}, None),
                            urllib.error.HTTPError("url", 403, "", {}, None)):
            with self.subTest(observation=observation):
                with self.assertRaises(TimeoutError):
                    self.wait(itertools.repeat(observation))

    def test_failed_job_never_allows_startup(self):
        with self.assertRaises(RuntimeError):
            self.wait([job("Failed")])

    def test_transient_observation_errors_retry(self):
        client, elapsed = self.wait([OSError("network"), ValueError("bad JSON"),
                                     job("Complete")])
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(elapsed, 10)

    def test_observation_errors_do_not_disclose_response_details(self):
        clock = Clock()
        client = Mock(namespace="ns")
        client.get.side_effect = ValueError("secret response body")
        log = Mock()
        with self.assertRaises(TimeoutError) as raised:
            gate.wait_for_job(client, "job", 6, 5, clock=clock,
                              sleep=clock.sleep, log=log)
        self.assertNotIn("secret response body", str(raised.exception))
        self.assertNotIn("secret response body", str(log.call_args_list))

    def test_late_success_does_not_escape_deadline(self):
        clock = Clock()
        client = Mock(namespace="ns")
        def late(*args):
            clock.now = 21
            return job("Complete")
        client.get.side_effect = late
        with self.assertRaises(TimeoutError):
            gate.wait_for_job(client, "job", 20, 5, clock=clock,
                              sleep=clock.sleep, log=Mock())


class RollbackCleanupTests(unittest.TestCase):
    name = "release-migrate-r7"

    def test_resource_paths_escape_and_limit_cleanup_to_the_target_migration(self):
        self.assertEqual(rollback.resource_paths("ns/other", "name/other"), [
            "/apis/batch/v1/namespaces/ns%2Fother/jobs/name%2Fother",
            "/api/v1/namespaces/ns%2Fother/configmaps/name%2Fother-config",
            "/api/v1/namespaces/ns%2Fother/configmaps/name%2Fother-gate",
            "/apis/rbac.authorization.k8s.io/v1/namespaces/ns%2Fother/rolebindings/name%2Fother",
            "/apis/rbac.authorization.k8s.io/v1/namespaces/ns%2Fother/roles/name%2Fother",
        ])

    def test_job_is_absent_before_any_config_or_rbac_cleanup(self):
        paths = rollback.resource_paths("ns", self.name)
        job_path = paths[0]
        observations = iter([True, False])
        deleted = absent = False
        clock = Clock()
        def request(method, path, deadline):
            nonlocal deleted, absent
            self.assertIn(path, paths)
            self.assertEqual(deadline, 10)
            if path == job_path:
                if method == "DELETE":
                    deleted = True
                    return True
                self.assertTrue(deleted)
                exists = next(observations)
                absent = not exists
                return exists
            self.assertTrue(absent)
            return method == "DELETE"
        client = Mock()
        client.request.side_effect = request
        rollback.cleanup(client, "ns", self.name, 10, clock=clock, sleep=clock.sleep)
        expected = [("DELETE", job_path, 10), ("GET", job_path, 10),
                    ("GET", job_path, 10)]
        expected.extend((method, path, 10) for path in paths[1:]
                        for method in ("DELETE", "GET"))
        self.assertEqual([call.args for call in client.request.call_args_list], expected)
        self.assertEqual(clock.now, 2)

    def test_cleanup_of_absent_resources_is_idempotent(self):
        client = Mock()
        client.request.return_value = False
        sleep = Mock()
        rollback.cleanup(client, "ns", self.name, 10, clock=lambda: 0, sleep=sleep)
        self.assertEqual(
            [call.args for call in client.request.call_args_list],
            [(method, path, 10) for path in rollback.resource_paths("ns", self.name)
             for method in ("DELETE", "GET")],
        )
        sleep.assert_not_called()

    def test_stuck_job_blocks_all_config_and_rbac_cleanup(self):
        job_path = rollback.resource_paths("ns", self.name)[0]
        client = Mock()
        client.request.return_value = True
        clock = Clock()
        with self.assertRaises(TimeoutError):
            rollback.cleanup(client, "ns", self.name, 3,
                             clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 3)
        self.assertEqual(
            [call.args for call in client.request.call_args_list],
            [("DELETE", job_path, 3), ("GET", job_path, 3), ("GET", job_path, 3)],
        )

    def test_config_cleanup_uses_remaining_job_deadline(self):
        paths = rollback.resource_paths("ns", self.name)
        observations = iter([True, False])
        client = Mock()
        clock = Clock()
        def request(method, path, deadline):
            self.assertEqual(deadline, 5)
            if method == "GET" and path == paths[0]:
                return next(observations)
            return True
        client.request.side_effect = request
        with self.assertRaises(TimeoutError):
            rollback.cleanup(client, "ns", self.name, 5,
                             clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 5)
        self.assertEqual(
            [call.args[1] for call in client.request.call_args_list
             if call.args[0] == "DELETE"],
            paths[:2],
        )
        self.assertEqual(client.request.call_args.args[:2], ("GET", paths[1]))

    def test_late_absence_does_not_escape_shared_deadline(self):
        paths = rollback.resource_paths("ns", self.name)
        for late_path in paths:
            with self.subTest(late_path=late_path):
                clock = Clock()
                client = Mock()
                def request(method, path, deadline):
                    self.assertEqual(deadline, 3)
                    if method == "GET":
                        clock.now = 3 if path == late_path else clock.now + 0.25
                    return False
                client.request.side_effect = request
                with self.assertRaises(TimeoutError):
                    rollback.cleanup(client, "ns", self.name, 3,
                                     clock=clock, sleep=clock.sleep)
                self.assertEqual(client.request.call_args.args[:2], ("GET", late_path))

    def test_permission_or_transport_failure_stops_cleanup(self):
        paths = rollback.resource_paths("ns", self.name)
        for failure in (RuntimeError("forbidden"), OSError("network")):
            for failing_path in paths:
                for failing_method in ("DELETE", "GET"):
                    with self.subTest(failure=failure, path=failing_path,
                                      method=failing_method):
                        client = Mock()
                        def request(method, path, deadline):
                            if (method, path) == (failing_method, failing_path):
                                raise failure
                            return False
                        client.request.side_effect = request
                        with self.assertRaises(type(failure)):
                            rollback.cleanup(client, "ns", self.name, 10,
                                             clock=lambda: 0, sleep=Mock())
                        self.assertEqual(client.request.call_args.args[:2],
                                         (failing_method, failing_path))
                        self.assertTrue(all(call.args[1] in paths[:paths.index(failing_path) + 1]
                                            for call in client.request.call_args_list))


class RollbackClientTests(unittest.TestCase):
    def client(self):
        client = rollback.Client.__new__(rollback.Client)
        client.base = "https://kubernetes:443"
        client.credentials = MagicMock()
        (client.credentials / "token").read_text.return_value = "private-token\n"
        client.opener = MagicMock()
        return client

    def test_delete_uses_foreground_propagation_and_shared_request_budget(self):
        client = self.client()
        with patch.object(rollback.time, "monotonic", return_value=8):
            self.assertTrue(client.request("DELETE", "/exact/path", 10))
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://kubernetes:443/exact/path")
        self.assertEqual(json.loads(request.data), {"propagationPolicy": "Foreground"})
        self.assertEqual(request.get_header("Authorization"), "Bearer private-token")
        self.assertEqual(client.opener.open.call_args.kwargs["timeout"], 2)

    def test_only_not_found_is_absence_and_error_bodies_are_not_disclosed(self):
        for status in (404, 403, 500):
            with self.subTest(status=status):
                client = self.client()
                body = Mock()
                client.opener.open.side_effect = urllib.error.HTTPError(
                    "https://kubernetes", status, "private response", {}, body)
                with patch.object(rollback.time, "monotonic", return_value=0):
                    if status == 404:
                        self.assertFalse(client.request("GET", "/exact/path", 10))
                    else:
                        with self.assertRaises(RuntimeError) as raised:
                            client.request("GET", "/exact/path", 10)
                        self.assertNotIn("private", str(raised.exception))
                body.close.assert_called_once_with()

    def test_redirects_never_forward_credentials(self):
        handler = rollback.NoRedirect()
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(Mock(full_url="https://kubernetes"), Mock(),
                                     302, "redirect", {}, "https://elsewhere")


if __name__ == "__main__":
    unittest.main()
