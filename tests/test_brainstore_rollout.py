import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse


FILES = Path(__file__).resolve().parents[1] / "braintrust/files"
shared_spec = importlib.util.spec_from_file_location(
    "brainstore_gate", FILES / "brainstore-startup-gate.py")
shared = importlib.util.module_from_spec(shared_spec)
shared_spec.loader.exec_module(shared)
spec = importlib.util.spec_from_file_location("rollout", FILES / "brainstore-rollout.py")
rollout = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"brainstore_gate": shared}):
    spec.loader.exec_module(rollout)


def target(role="reader", **overrides):
    return {
        "deployment": "custom-" + role,
        "container": "brainstore-" + role,
        "revision": "17",
        "image": "registry.example:5000/brainstore:custom-build",
        "configChecksum": "b" * 64,
        **overrides,
    }


def pod(expected=None, name="new", is_ready=True, **metadata):
    expected = target() if expected is None else expected
    return {
        "metadata": {
            "name": name,
            "labels": {"app": expected["deployment"]},
            "annotations": {
                "braintrust.dev/release-revision": expected["revision"],
                "checksum/config": expected["configChecksum"],
            },
            **metadata,
        },
        "spec": {"containers": [{"name": expected["container"], "image": expected["image"]}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True" if is_ready else "False"}],
            "containerStatuses": [{
                "name": expected["container"], "image": expected["image"],
                "ready": is_ready, "state": {"running": {}},
            }],
        },
    }


def deployment(expected=None, replicas=1):
    expected = target() if expected is None else expected
    current = pod(expected)
    return {
        "metadata": {"name": expected["deployment"], "generation": 7},
        "spec": {
            "replicas": replicas,
            "template": {"metadata": {"annotations": current["metadata"]["annotations"]},
                         "spec": current["spec"]},
        },
        "status": {
            "observedGeneration": 7,
            "updatedReplicas": replicas,
            "readyReplicas": replicas,
            "availableReplicas": replicas,
            "replicas": replicas,
        },
    }


class DeploymentTests(unittest.TestCase):
    def check(self, resource=None, pods=None, expected=None):
        expected = target() if expected is None else expected
        return rollout.check_deployment(
            deployment(expected) if resource is None else resource,
            [pod(expected)] if pods is None else pods, expected)

    def test_exact_ready_release_passes(self):
        self.assertIsNone(self.check())

    def test_old_fully_ready_spec_does_not_pass_before_helm_updates_it(self):
        old = target(revision="16")
        self.assertIsNotNone(self.check(deployment(old), [pod(old)]))

    def test_config_only_change_requires_updated_template_and_pods(self):
        old = target(configChecksum="a" * 64)
        self.assertIsNotNone(self.check(deployment(old), [pod(old)]))
        self.assertIsNotNone(self.check(pods=[pod(old, "old")]))
        self.assertIsNone(self.check())

    def test_old_ready_writer_cannot_release_a_pending_same_image_replacement(self):
        expected = target("writer")
        old = target("writer", configChecksum="a" * 64)
        pending = pod(expected, "replacement", False)
        pending["status"]["phase"] = "Pending"
        resource = deployment(expected)
        resource["status"]["updatedReplicas"] = 0
        self.assertIsNotNone(self.check(resource, [pod(old, "old-writer"), pending], expected))
        self.assertIsNotNone(self.check(pods=[pending], expected=expected))

    def test_revision_only_env_rollout_cannot_use_previous_ready_pod(self):
        old = target(revision="16")
        self.assertIsNotNone(self.check(pods=[pod(old, "old")]))

    def test_missing_and_wrong_identity_fields_fail_closed_on_template_and_pods(self):
        fields = ("braintrust.dev/release-revision", "checksum/config")
        for location in ("template", "pod"):
            for annotation in fields:
                for value in (None, "wrong"):
                    with self.subTest(location=location, annotation=annotation, value=value):
                        resource = deployment()
                        current = pod()
                        subject = resource["spec"]["template"] if location == "template" else current
                        if value is None:
                            del subject["metadata"]["annotations"][annotation]
                        else:
                            subject["metadata"]["annotations"][annotation] = value
                        self.assertIsNotNone(self.check(resource, [current]))

    def test_image_must_match_exactly_in_template_and_pod_spec(self):
        for location in ("template", "pod"):
            with self.subTest(location=location):
                resource = deployment()
                current = pod()
                subject = resource["spec"]["template"] if location == "template" else current
                subject["spec"]["containers"][0]["image"] = "registry.example/brainstore:other"
                self.assertIsNotNone(self.check(resource, [current]))

    def test_exact_images_do_not_need_semver_tags(self):
        for image in ("registry/brainstore:v2.15.0", "registry/brainstore:latest",
                      "registry/brainstore:deadbeef", "registry/brainstore:v2.15.0-rc.1",
                      "registry:5000/brainstore@sha256:" + "a" * 64,
                      "registry/brainstore:custom@sha256:" + "b" * 64):
            with self.subTest(image=image):
                self.assertIsNone(self.check(expected=target(image=image)))

    def test_runtime_image_alias_neither_blocks_nor_overrides_the_pod_spec(self):
        current = pod()
        current["status"]["containerStatuses"][0]["image"] = "containerd://sha256:resolved"
        self.assertIsNone(self.check(pods=[current]))
        current["spec"]["containers"][0]["image"] = "registry/brainstore:old"
        self.assertIsNotNone(self.check(pods=[current]))

    def test_missing_expected_spec_container_fails_closed(self):
        for location in ("template", "pod"):
            with self.subTest(location=location):
                resource = deployment()
                current = pod()
                subject = resource["spec"]["template"] if location == "template" else current
                subject["spec"]["containers"][0]["name"] = "sidecar"
                self.assertIsNotNone(self.check(resource, [current]))

    def test_current_controller_observation_is_required(self):
        for observed in (None, 6):
            with self.subTest(observed=observed):
                resource = deployment()
                if observed is None:
                    del resource["status"]["observedGeneration"]
                else:
                    resource["status"]["observedGeneration"] = observed
                self.assertIsNotNone(self.check(resource))

    def test_deleted_deployment_blocks(self):
        resource = deployment()
        resource["metadata"]["deletionTimestamp"] = "2026-11-01T00:00:00Z"
        self.assertIsNotNone(self.check(resource))

    def test_each_controller_replica_counter_must_equal_desired(self):
        for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            for count in (None, 0, 2):
                with self.subTest(counter=counter, count=count):
                    resource = deployment()
                    if count is None:
                        del resource["status"][counter]
                    else:
                        resource["status"][counter] = count
                    self.assertIsNotNone(self.check(resource))

    def test_min_ready_seconds_requires_controller_availability(self):
        resource = deployment()
        resource["spec"]["minReadySeconds"] = 30
        resource["status"]["availableReplicas"] = 0
        self.assertIsNotNone(self.check(resource))
        resource["status"]["availableReplicas"] = 1
        self.assertIsNone(self.check(resource))

    def test_current_spec_scaling_is_honored_without_target_replica_snapshot(self):
        resource = deployment(replicas=3)
        self.assertIsNone(self.check(resource, [pod(name=str(index)) for index in range(3)]))
        self.assertIsNotNone(self.check(resource, [pod()]))
        self.assertIsNotNone(self.check(pods=[pod(), pod(name="surge")]))

    def test_missing_replicas_defaults_to_one(self):
        resource = deployment()
        del resource["spec"]["replicas"]
        self.assertIsNone(self.check(resource))
        self.assertIsNotNone(self.check(resource, []))

    def test_explicit_zero_requires_no_live_backends_and_zero_status_counts(self):
        resource = deployment(replicas=0)
        self.assertIsNone(self.check(resource, []))
        self.assertIsNotNone(self.check(resource, [pod()]))
        for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            with self.subTest(counter=counter):
                stale = deployment(replicas=0)
                stale["status"][counter] = 1
                self.assertIsNotNone(self.check(stale, []))
        resource["status"] = {"observedGeneration": 7}
        self.assertIsNone(self.check(resource, []))

    def test_zero_still_requires_current_release_template_and_observation(self):
        resource = deployment(target(revision="16"), replicas=0)
        self.assertIsNotNone(self.check(resource, []))
        resource = deployment(replicas=0)
        resource["status"]["observedGeneration"] = 6
        self.assertIsNotNone(self.check(resource, []))

    def test_terminal_pods_are_ignored_even_when_old_or_terminating(self):
        for phase in ("Failed", "Succeeded"):
            with self.subTest(phase=phase):
                old = pod(target(revision="16"), "old", False,
                          deletionTimestamp="2026-11-01T00:00:00Z")
                old["status"]["phase"] = phase
                self.assertIsNone(self.check(pods=[pod(), old]))
                self.assertIsNone(self.check(deployment(replicas=0), [old]))

    def test_terminating_matching_backend_blocks_even_if_unready(self):
        for is_ready in (True, False):
            with self.subTest(is_ready=is_ready):
                terminating = pod(name="terminating", is_ready=is_ready,
                                  deletionTimestamp="2026-11-01T00:00:00Z")
                self.assertIsNotNone(self.check(pods=[pod(), terminating]))
                self.assertIsNotNone(self.check(deployment(replicas=0), [terminating]))

    def test_ready_requires_running_expected_container_and_pod_condition(self):
        for missing in ("phase", "condition", "container", "running", "ready"):
            with self.subTest(missing=missing):
                current = pod()
                if missing == "phase":
                    current["status"]["phase"] = "Pending"
                elif missing == "condition":
                    current["status"]["conditions"] = []
                elif missing == "container":
                    current["status"]["containerStatuses"][0]["name"] = "sidecar"
                elif missing == "running":
                    current["status"]["containerStatuses"][0]["state"] = {"waiting": {}}
                else:
                    current["status"]["containerStatuses"][0]["ready"] = False
                self.assertIsNotNone(self.check(pods=[current]))


class FleetTests(unittest.TestCase):
    def test_every_enabled_role_uses_its_custom_service_selector_and_current_spec(self):
        targets = [target(role, deployment="tenant-pool-" + role)
                   for role in ("reader", "writer", "fastreader", "automationwriter")]
        resources = {expected["deployment"]: deployment(expected, replicas=0 if index == 3 else 1)
                     for index, expected in enumerate(targets)}
        backends = {"app=" + expected["deployment"]: [pod(expected)] if index != 3 else []
                    for index, expected in enumerate(targets)}
        client = Mock()
        client.deployment.side_effect = lambda name, deadline: resources[name]
        client.pods.side_effect = lambda selector, deadline: backends[selector]
        self.assertIsNone(rollout.check_fleet(client, targets, 100))
        for expected in targets:
            with self.subTest(role=expected["container"]):
                selector = "app=" + expected["deployment"]
                current = backends[selector]
                backends[selector] = [pod({**expected, "revision": "16"}, "old")]
                self.assertIsNotNone(rollout.check_fleet(client, targets, 100))
                backends[selector] = current

    def test_unready_optional_role_blocks_the_fleet(self):
        targets = [target(role) for role in ("reader", "writer", "fastreader", "automationwriter")]
        client = Mock()
        client.deployment.side_effect = [deployment(expected) for expected in targets]
        client.pods.side_effect = [[pod(expected, is_ready=index != 3)]
                                  for index, expected in enumerate(targets)]
        self.assertIsNotNone(rollout.check_fleet(client, targets, 100))

    def test_old_stray_backend_on_later_api_page_blocks_without_owner_filtering(self):
        expected = target(deployment="tenant-pool-reader")
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}), \
             patch.object(shared.ssl, "create_default_context"):
            client = rollout.KubernetesClient("custom namespace", "/synthetic")
        stray = pod(target(deployment=expected["deployment"], revision="16"), "stray")
        resources = {
            "/apis/apps/v1/namespaces/custom%20namespace/deployments/tenant-pool-reader":
                deployment(expected),
        }
        pages = {
            "": {"items": [pod(expected)], "metadata": {"continue": "next-page"}},
            "next-page": {"items": [stray]},
        }

        def get_resource(path, deadline):
            if path in resources:
                return resources[path]
            parsed = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parsed.query)
            if (parsed.path != "/api/v1/namespaces/custom%20namespace/pods"
                    or query.get("labelSelector") != ["app=" + expected["deployment"]]):
                raise AssertionError("unexpected Kubernetes resource request")
            return pages.pop(query.get("continue", [""])[0])

        client.get = get_resource
        self.assertIsNotNone(rollout.check_fleet(client, [expected], 100))

    def test_extra_matching_stray_is_not_hidden_by_ready_controller_status(self):
        client = Mock()
        client.deployment.return_value = deployment()
        client.pods.return_value = [pod(), pod(name="stray")]
        self.assertIsNotNone(rollout.check_fleet(client, [target()], 100))


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def healthy_client():
    client = Mock()
    client.deployment.return_value = deployment()
    client.pods.return_value = [pod()]
    return client


class WaitTests(unittest.TestCase):
    def wait(self, client, clock=None, timeout=60, interval=10, targets=None, log=None):
        clock = FakeClock() if clock is None else clock
        log = Mock() if log is None else log
        rollout.wait_for_fleet(client, [target()] if targets is None else targets,
                              timeout, interval, clock.clock, clock.sleep, log)
        return clock, log

    def test_requires_two_real_successful_fleet_observations(self):
        client = healthy_client()
        clock, _ = self.wait(client)
        self.assertEqual(client.deployment.call_count, 2)
        self.assertEqual(client.pods.call_count, 2)
        self.assertEqual(clock.now, 10)

    def test_unhealthy_observation_resets_confirmation(self):
        client = healthy_client()
        client.pods.side_effect = [[pod()], [pod(is_ready=False)], [pod()], [pod()]]
        clock, _ = self.wait(client)
        self.assertEqual(client.pods.call_count, 4)
        self.assertEqual(clock.now, 30)

    def test_transient_read_failure_resets_confirmation(self):
        client = healthy_client()
        client.deployment.side_effect = [deployment(), OSError("offline"), deployment(), deployment()]
        clock, _ = self.wait(client)
        self.assertEqual(client.deployment.call_count, 4)
        self.assertEqual(client.pods.call_count, 3)
        self.assertEqual(clock.now, 30)

    def test_missing_resources_rbac_throttling_and_server_errors_are_retried(self):
        bodies = [io.StringIO("sensitive response body") for _ in range(4)]
        errors = [urllib.error.HTTPError("https://api/deployments/reader?secret=hidden", code,
                                         "sensitive reason", None, body)
                  for code, body in zip((404, 403, 429, 503), bodies)]
        client = healthy_client()
        client.deployment.side_effect = errors + [deployment(), deployment()]
        clock, log = self.wait(client)
        self.assertEqual(clock.now, 50)
        self.assertTrue(all(body.closed for body in bodies))
        messages = "\n".join(call.args[0] for call in log.call_args_list)
        self.assertNotIn("sensitive", messages)
        self.assertNotIn("secret", messages)

    def test_read_and_decode_errors_are_retried_without_leaking_details(self):
        client = healthy_client()
        client.deployment.side_effect = [
            urllib.error.URLError("secret transport detail"), ValueError("secret JSON"),
            KeyError("secret field"), TypeError("secret object"), deployment(), deployment(),
        ]
        clock, log = self.wait(client)
        self.assertEqual(clock.now, 50)
        messages = "\n".join(call.args[0] for call in log.call_args_list)
        self.assertNotIn("secret", messages)

    def test_pod_list_failure_is_retried_instead_of_using_previous_ready_result(self):
        client = healthy_client()
        client.pods.side_effect = [[pod()], urllib.error.URLError("offline"), [pod()], [pod()]]
        clock, _ = self.wait(client)
        self.assertEqual(client.pods.call_count, 4)
        self.assertEqual(clock.now, 30)

    def test_old_ready_config_never_completes_and_sleep_is_bounded(self):
        client = healthy_client()
        client.pods.return_value = [pod(target(configChecksum="a" * 64), "old")]
        clock = FakeClock()
        with self.assertRaises(TimeoutError):
            self.wait(client, clock, timeout=25)
        self.assertEqual(clock.now, 25)
        self.assertEqual(clock.sleeps, [10, 10, 5])
        self.assertEqual(client.deployment.call_count, 3)

    def test_persistent_api_failure_times_out_with_bounded_diagnostics(self):
        client = healthy_client()
        client.deployment.side_effect = [urllib.error.HTTPError(
            "https://api/" + "x" * 1000 + "?secret=hidden", 429,
            "sensitive reason", None, io.StringIO("sensitive body")) for _ in range(3)]
        clock = FakeClock()
        with self.assertRaises(TimeoutError) as error:
            self.wait(client, clock, timeout=25)
        self.assertLess(len(str(error.exception)), 400)
        self.assertNotIn("sensitive", str(error.exception))
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(clock.now, 25)

    def test_successful_response_after_deadline_cannot_complete(self):
        client = healthy_client()
        clock = FakeClock()
        observations = iter((0, 9))

        def observe(selector, deadline):
            clock.now += next(observations)
            return [pod()]

        client.pods.side_effect = observe
        with self.assertRaises(TimeoutError):
            self.wait(client, clock, timeout=10, interval=2)
        self.assertEqual(client.pods.call_count, 2)
        self.assertEqual(clock.now, 11)

    def test_all_roles_requests_and_confirmations_share_one_deadline(self):
        targets = [target("reader"), target("writer")]
        expected_by_name = {expected["deployment"]: expected for expected in targets}
        client = Mock()
        clock = FakeClock()
        deadlines = []

        def get_deployment(name, deadline):
            deadlines.append(deadline)
            clock.now += 1
            return deployment(expected_by_name[name])

        def get_pods(selector, deadline):
            deadlines.append(deadline)
            clock.now += 1
            return [pod(expected_by_name[selector.removeprefix("app=")])]

        client.deployment.side_effect = get_deployment
        client.pods.side_effect = get_pods
        self.wait(client, clock, timeout=20, interval=3, targets=targets)
        self.assertEqual(deadlines, [20] * 8)
        self.assertEqual(clock.now, 11)

    def test_scaling_between_confirmations_uses_each_current_spec(self):
        client = healthy_client()
        client.deployment.side_effect = [deployment(replicas=2), deployment()]
        client.pods.side_effect = [[pod(name="one"), pod(name="two")], [pod()]]
        self.wait(client)
        self.assertEqual(client.deployment.call_count, 2)


if __name__ == "__main__":
    unittest.main()
