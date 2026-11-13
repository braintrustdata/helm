import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error

FILES = Path(__file__).resolve().parents[1] / "braintrust/files"
# The chart mounts the shared runtime under these import names.
for name, file in [
    ("brainstore_gate", "brainstore-startup-gate.py"),
    ("job_gate", "job-gate.py"),
]:
    spec = importlib.util.spec_from_file_location(name, FILES / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
gate = sys.modules["job_gate"]
shared = sys.modules["brainstore_gate"]


def job(*conditions, **metadata):
    return {"metadata": metadata, "status": {"conditions": [
        {"type": condition, "status": "True"} for condition in conditions
    ]}}


def ordering(name="release-migrate-" + "a" * 32):
    return {"data": {"ordering.json": json.dumps({
        "job": name,
        "targets": [{"deployment": "reader", "container": "brainstore-reader",
                     "template": "b" * 64, "replicas": 1, "minReadySeconds": 0}],
    })}}


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class JobGateTests(unittest.TestCase):
    def client(self, observations, configs=None):
        observations = iter(observations)
        configs = iter(configs) if configs is not None else itertools.repeat(ordering())

        def get(path, deadline):
            if "/configmaps/" in path:
                resource = next(configs)
            elif "/jobs/" in path:
                resource = next(observations)
            else:
                raise AssertionError(f"unexpected API path: {path}")
            if isinstance(resource, BaseException):
                raise resource
            return resource

        client = Mock(namespace="test-namespace")
        client.get.side_effect = get
        return client

    def wait(self, observations, timeout=20, configs=None):
        clock = Clock()
        client = self.client(observations, configs)
        gate.wait_for_job(client, "release-ordering", timeout, 5,
                          clock=clock, sleep=clock.sleep, log=Mock())
        return client, clock.now

    def test_only_complete_condition_counts_as_success(self):
        for resource in ({}, job(), job("SuccessCriteriaMet"),
                         {"status": {"succeeded": 1}},
                         {"status": {"conditions": [{"type": "Complete", "status": "False"}]}},
                         job("Complete", deletionTimestamp="now")):
            with self.subTest(resource=resource):
                self.assertFalse(shared.job_complete(resource))
        self.assertTrue(shared.job_complete(job("Complete")))

    def test_failure_takes_precedence_over_completion_and_deletion(self):
        for condition in ("Failed", "FailureTarget"):
            for metadata in ({}, {"deletionTimestamp": "now"}):
                with self.subTest(condition=condition, metadata=metadata):
                    with self.assertRaises(RuntimeError):
                        shared.job_complete(job(condition, "Complete", **metadata))

    def test_malformed_job_observations_fail_closed(self):
        for resource in (None, [], {"metadata": None}, {"status": None},
                         {"status": {"conditions": None}},
                         {"status": {"conditions": {}}},
                         {"status": {"conditions": [None]}},
                         {"status": {"conditions": ["Complete"]}}):
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                shared.job_complete(resource)

    def test_exact_job_wait_retries_missing_job_rbac_and_server_errors(self):
        errors = [urllib.error.HTTPError("url", code, "error", {}, io.BytesIO())
                  for code in (404, 403, 500)]
        client, elapsed = self.wait(errors + [job("Complete")])
        self.assertEqual(elapsed, 15)
        self.assertEqual([call.args for call in client.get.call_args_list], [
            ("/api/v1/namespaces/test-namespace/configmaps/release-ordering", 20),
            ("/apis/batch/v1/namespaces/test-namespace/jobs/release-migrate-" + "a" * 32, 20),
        ] * 4)

    def test_retained_success_does_not_wait_for_another_observation(self):
        client, elapsed = self.wait([job("Complete")])
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(elapsed, 0)

    def test_running_job_then_success(self):
        _, elapsed = self.wait([job(), job("Complete")])
        self.assertEqual(elapsed, 5)

    def test_current_config_job_is_selected_on_every_observation(self):
        client, elapsed = self.wait([job(), job("Complete")], configs=[
            ordering("old-content-job"), ordering("new-content-job"),
        ])
        self.assertEqual(elapsed, 5)
        paths = [call.args[0] for call in client.get.call_args_list]
        self.assertEqual(paths, [
            "/api/v1/namespaces/test-namespace/configmaps/release-ordering",
            "/apis/batch/v1/namespaces/test-namespace/jobs/old-content-job",
            "/api/v1/namespaces/test-namespace/configmaps/release-ordering",
            "/apis/batch/v1/namespaces/test-namespace/jobs/new-content-job",
        ])

    def test_missing_config_does_not_reuse_previously_selected_job(self):
        client, elapsed = self.wait([job(), job("Complete")], configs=[
            ordering("old-content-job"), {"data": {}}, ordering("new-content-job"),
        ])
        self.assertEqual(elapsed, 10)
        paths = [call.args[0] for call in client.get.call_args_list if "/jobs/" in call.args[0]]
        self.assertEqual(paths, [
            "/apis/batch/v1/namespaces/test-namespace/jobs/old-content-job",
            "/apis/batch/v1/namespaces/test-namespace/jobs/new-content-job",
        ])

    def test_missing_or_forbidden_ordering_config_retries_without_reading_job(self):
        errors = [urllib.error.HTTPError("url", code, "error", {}, io.BytesIO())
                  for code in (404, 403)]
        client, elapsed = self.wait([job("Complete")], configs=errors + [ordering()])
        self.assertEqual(elapsed, 10)
        self.assertEqual(client.get.call_count, 4)
        self.assertEqual(sum("/jobs/" in call.args[0] for call in client.get.call_args_list), 1)

    def test_missing_or_malformed_config_never_allows_startup(self):
        invalid_target = ordering()
        config = json.loads(invalid_target["data"]["ordering.json"])
        config["targets"][0]["replicas"] = True
        invalid_target["data"]["ordering.json"] = json.dumps(config)
        for resource in ({}, {"data": {}}, {"data": {"ordering.json": "not JSON"}},
                         ordering(""), invalid_target,
                         {"data": {"ordering.json": '{"job":"job","targets":[]}'}}):
            with self.subTest(resource=resource):
                client = self.client([], itertools.repeat(resource))
                clock = Clock()
                with self.assertRaises(TimeoutError):
                    gate.wait_for_job(client, "release-ordering", 20, 5,
                                      clock=clock, sleep=clock.sleep, log=Mock())
                self.assertTrue(all("/configmaps/" in call.args[0]
                                    for call in client.get.call_args_list))

    def test_running_missing_or_forbidden_job_never_allows_startup(self):
        for observation in (job(), urllib.error.HTTPError("url", 404, "", {}, io.BytesIO()),
                            urllib.error.HTTPError("url", 403, "", {}, io.BytesIO())):
            with self.subTest(observation=observation), self.assertRaises(TimeoutError):
                self.wait(itertools.repeat(observation))

    def test_failed_job_never_allows_startup(self):
        with self.assertRaises(RuntimeError):
            self.wait([job("Failed")])

    def test_transient_observation_errors_retry(self):
        client, elapsed = self.wait([OSError("network"), ValueError("bad JSON"),
                                     job("Complete")])
        self.assertEqual(client.get.call_count, 6)
        self.assertEqual(elapsed, 10)

    def test_observation_errors_do_not_disclose_response_details(self):
        for config_failure in (False, True):
            with self.subTest(config_failure=config_failure):
                clock = Clock()
                errors = itertools.repeat(ValueError("secret response body"))
                client = self.client([], errors) if config_failure else self.client(errors)
                log = Mock()
                with self.assertRaises(TimeoutError) as raised:
                    gate.wait_for_job(client, "release-ordering", 6, 5, clock=clock,
                                      sleep=clock.sleep, log=log)
                self.assertNotIn("secret response body", str(raised.exception))
                self.assertNotIn("secret response body", str(log.call_args_list))

    def test_job_path_escapes_current_job_name(self):
        client, _ = self.wait([job("Complete")], configs=[ordering("name/other")])
        self.assertEqual(client.get.call_args.args[0],
                         "/apis/batch/v1/namespaces/test-namespace/jobs/name%2Fother")

    def test_late_success_does_not_escape_deadline(self):
        clock = Clock()
        client = Mock(namespace="ns")

        def late(path, deadline):
            if "/configmaps/" in path:
                return ordering()
            clock.now = 21
            return job("Complete")

        client.get.side_effect = late
        with self.assertRaises(TimeoutError):
            gate.wait_for_job(client, "release-ordering", 20, 5, clock=clock,
                              sleep=clock.sleep, log=Mock())


class MainTests(unittest.TestCase):
    def environment(self):
        return {"ORDERING_CONFIGMAP": "release-ordering", "JOB_TIMEOUT_SECONDS": "20",
                "JOB_POLL_SECONDS": "5", "POD_NAMESPACE": "custom"}

    def test_uses_stable_ordering_configmap_not_fixed_job_environment(self):
        with patch.dict(os.environ, self.environment(), clear=True), \
             patch.object(gate, "KubernetesClient") as client, \
             patch.object(gate, "wait_for_job") as wait:
            gate.main()
        client.assert_called_once_with("custom", "/var/run/migration-gate")
        wait.assert_called_once_with(client.return_value, "release-ordering", 20, 5)

    def test_missing_or_invalid_environment_fails_before_polling(self):
        invalid = {
            "ORDERING_CONFIGMAP": (None, ""),
            "JOB_TIMEOUT_SECONDS": (None, "", "invalid", "0", "-1", "5"),
            "JOB_POLL_SECONDS": (None, "", "invalid", "0", "-1", "20"),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    environment = self.environment()
                    if value is None:
                        del environment[field]
                    else:
                        environment[field] = value
                    with patch.dict(os.environ, environment, clear=True), \
                         patch.object(gate, "wait_for_job") as wait, \
                         self.assertRaises((KeyError, ValueError)):
                        gate.main()
                    wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
