import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error


SCRIPT = Path(__file__).resolve().parents[1] / "braintrust/files/brainstore_startup_gate.py"
spec = importlib.util.spec_from_file_location("gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def pod(version="v2.15.0", uid="new", is_ready=True, **metadata):
    return {
        "metadata": {"name": uid, "uid": uid, **metadata},
        "spec": {"containers": [{"name": "brainstore-reader", "image": "registry/brainstore:" + version}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True" if is_ready else "False"}],
            "containerStatuses": [{"name": "brainstore-reader", "image": "registry/brainstore:" + version,
                                   "ready": is_ready, "state": {"running": {}}}],
        },
    }


def deployment(version="v2.15.0", replicas=1):
    return {
        "metadata": {"name": "reader", "uid": "reader-deployment", "generation": 2},
        "spec": {"replicas": replicas, "template": {"spec": pod(version)["spec"]}},
        "status": {"observedGeneration": 2, "availableReplicas": replicas},
    }


class CompatibilityTests(unittest.TestCase):
    def check(self, resource=None, pods=None, minimum="2.15.0"):
        return gate.check_deployment(resource or deployment(), pods if pods is not None else [pod()],
                                     "brainstore-reader", gate.release_version(minimum))

    def test_new_api_waits_when_helm_has_not_updated_brainstore_spec(self):
        self.assertIn("desired image", self.check(deployment("v2.14.0"), [pod("v2.14.0")]))

    def test_new_api_waits_for_incompatible_old_pods(self):
        self.assertIn("incompatible Pod old", self.check(pods=[pod(), pod("v2.14.0", "old")]))

    def test_incompatible_terminating_pod_blocks_even_when_unready(self):
        old = pod("v2.14.0", "old", False, deletionTimestamp="2026-09-30T12:00:00Z")
        self.assertIn("incompatible Pod old", self.check(pods=[pod(), old]))

    def test_terminal_pods_do_not_block(self):
        for phase in ("Succeeded", "Failed"):
            with self.subTest(phase=phase):
                old = pod("v2.14.0", "old", False)
                old["status"]["phase"] = phase
                self.assertIsNone(self.check(pods=[pod(), old]))

    def test_matching_ready_fleet_passes(self):
        self.assertIsNone(self.check())

    def test_requires_live_ready_capacity(self):
        self.assertIn("available compatible", self.check(pods=[pod(is_ready=False)]))
        self.assertIn("available compatible", self.check(deployment(replicas=2), [pod()]))

    def test_honors_deployment_min_ready_seconds_availability(self):
        resource = deployment()
        resource["status"]["availableReplicas"] = 0
        self.assertIn("available compatible", self.check(resource))

    def test_old_api_can_restart_against_newer_brainstore(self):
        self.assertIsNone(self.check(deployment("v2.16.0"), [pod("v2.16.0")], "2.15.0"))

    def test_old_api_can_restart_during_compatible_future_rollout(self):
        resource = deployment("v2.16.0")
        pods = [pod("v2.15.0", "old"), pod("v2.16.0", "new", False)]
        self.assertIsNone(self.check(resource, pods))

    def test_new_api_still_blocks_during_same_future_rollout(self):
        pods = [pod("v2.15.0", "old"), pod("v2.16.0", "new", False)]
        self.assertIn("incompatible", self.check(deployment("v2.16.0"), pods, "2.16.0"))

    def test_compatible_terminating_pod_does_not_block_restart(self):
        terminating = pod("v2.15.0", "old", False, deletionTimestamp="2026-09-30T12:00:00Z")
        self.assertIsNone(self.check(pods=[pod(), terminating]))

    def test_zero_replica_role_waits_for_remaining_pods(self):
        self.assertIsNone(self.check(deployment(replicas=0), []))
        self.assertIn("scaled-down", self.check(deployment(replicas=0), [pod()]))

    def test_unobserved_generation_does_not_pass(self):
        resource = deployment()
        resource["status"]["observedGeneration"] = 1
        self.assertIn("not observed", self.check(resource))

    def test_deleted_deployment_does_not_pass(self):
        resource = deployment()
        resource["metadata"]["deletionTimestamp"] = "2026-09-30T12:00:00Z"
        self.assertIn("being deleted", self.check(resource))

    def test_unknown_and_other_major_images_fail_closed(self):
        for version in ("latest", "deadbeef", "v3.0.0", "v2.15.0-rc.1"):
            with self.subTest(version=version):
                self.assertIn("incompatible", self.check(pods=[pod(version)]))

    def test_registry_port_and_tagged_digest_are_supported(self):
        image = "localhost:5000/brainstore:v2.15.0@sha256:abc"
        self.assertTrue(gate.compatible(image, (2, 15, 0)))
        self.assertFalse(gate.compatible("registry/brainstore@sha256:abc", (2, 15, 0)))

    def test_numeric_version_ordering(self):
        self.assertTrue(gate.compatible("brainstore:v2.15.10", (2, 15, 9)))
        self.assertFalse(gate.compatible("brainstore:v2.9.0", (2, 15, 0)))

    def test_stable_release_parser(self):
        self.assertEqual(gate.release_version("v2.15.0"), (2, 15, 0))
        for version in ("2.015.0", "2.15", "2.15.0\n", "2.15.0+build", "v2.15.0-rc.1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                gate.release_version(version)

    def test_missing_expected_container_fails_closed(self):
        wrong = pod()
        wrong["spec"]["containers"][0]["name"] = "sidecar"
        self.assertIn("incompatible", self.check(pods=[wrong]))

    def test_runtime_image_aliases_do_not_block_a_compatible_deployment_pod(self):
        for image in ("registry/brainstore:deadbeef", "registry/brainstore@sha256:abc"):
            with self.subTest(image=image):
                current = pod()
                current["status"]["containerStatuses"][0]["image"] = image
                self.assertIsNone(self.check(pods=[current]))

    def test_runtime_alias_does_not_override_an_incompatible_pod_spec(self):
        current = pod("v2.14.0")
        current["status"]["containerStatuses"][0]["image"] = "registry/brainstore:v2.15.0"
        self.assertIn("incompatible", self.check(pods=[current]))

    def test_pod_ready_without_expected_running_container_is_insufficient(self):
        current = pod()
        current["status"]["containerStatuses"] = []
        self.assertIn("available compatible", self.check(pods=[current]))


class FakeClock:
    def __init__(self):
        self.now = 0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class WaitTests(unittest.TestCase):
    def wait(self, observations, timeout=60):
        clock = FakeClock()
        log = Mock()
        with patch.object(gate, "check_fleet", side_effect=observations) as check:
            gate.wait_for_fleet(Mock(), [{"deployment": "reader", "container": "brainstore-reader"}],
                                (2, 15, 0), timeout, 15, clock.clock, clock.sleep, log)
        return clock, log, check

    def test_requires_two_successful_observations(self):
        clock, _, check = self.wait([None, None])
        self.assertEqual(check.call_count, 2)
        self.assertEqual(clock.now, 15)

    def test_failed_check_resets_confirmation(self):
        _, _, check = self.wait([None, "old pod remains", None, None])
        self.assertEqual(check.call_count, 4)

    def test_transient_missing_deployment_and_rbac_are_retried(self):
        errors = [urllib.error.HTTPError("https://api", code, "synthetic", None, None)
                  for code in (404, 403)]
        _, _, check = self.wait(errors + [None, None])
        self.assertEqual(check.call_count, 4)

    def test_timeout_never_releases_api(self):
        with self.assertRaisesRegex(TimeoutError, "old pod remains"):
            self.wait(["old pod remains"] * 3, timeout=30)

    def test_kubernetes_api_failure_never_releases_api(self):
        with self.assertRaisesRegex(TimeoutError, "HTTP 429"):
            self.wait([urllib.error.HTTPError("https://api", 429, "synthetic", None, None)] * 3, 30)


class FleetTests(unittest.TestCase):
    def test_compatible_deployment_and_pod_churn_do_not_delay_an_old_api(self):
        client = Mock()
        first = deployment("v2.15.0")
        later = deployment("v2.16.0")
        later["metadata"]["generation"] = 3
        later["status"]["observedGeneration"] = 3
        client.deployment.side_effect = [first, later]
        client.pods.side_effect = [[pod("v2.15.0", "old")], [pod("v2.16.0", "new")]]
        targets = [{"deployment": "reader", "container": "brainstore-reader"}]
        clock = FakeClock()
        gate.wait_for_fleet(client, targets, (2, 15, 0), 60, 15,
                            clock.clock, clock.sleep, Mock())
        self.assertEqual(client.deployment.call_count, 2)
        self.assertEqual(clock.now, 15)

    def test_checks_all_three_roles_and_includes_stray_service_backends(self):
        client = Mock()
        targets = []
        deployments = []
        pod_sets = []
        for role in ("reader", "writer", "fastreader"):
            name = "custom-" + role
            container = "brainstore-" + role
            targets.append({"deployment": name, "container": container})
            resource = deployment()
            resource["metadata"]["name"] = name
            resource["metadata"]["uid"] = name
            resource["spec"]["template"]["spec"]["containers"][0]["name"] = container
            current = pod(uid=role)
            current["spec"]["containers"][0]["name"] = container
            current["status"]["containerStatuses"][0]["name"] = container
            deployments.append(resource)
            pod_sets.append([current])
        client.deployment.side_effect = deployments
        client.pods.side_effect = pod_sets
        reason = gate.check_fleet(client, targets, (2, 15, 0), 100)
        self.assertIsNone(reason)
        self.assertEqual(client.deployment.call_count, 3)
        self.assertEqual([call.args[0] for call in client.pods.call_args_list],
                         ["app=custom-reader", "app=custom-writer", "app=custom-fastreader"])
        # A manually labelled Pod can receive Service traffic without belonging
        # to a ReplicaSet, so it must not be filtered out by owner references.
        stray = pod("v2.14.0", "stray")
        client.deployment.side_effect = deployments
        client.pods.side_effect = [[pod(), stray]]
        reason = gate.check_fleet(client, targets, (2, 15, 0), 100)
        self.assertIn("stray", reason)


class ClientTests(unittest.TestCase):
    def client(self, directory, host="10.0.0.1"):
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": host}), \
             patch.object(gate.ssl, "create_default_context"):
            return gate.KubernetesClient("custom namespace", directory)

    def test_reloads_rotating_token_and_bounds_request_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("synthetic-token-1")
            client = self.client(directory)
            client.opener = Mock()
            client.opener.open.side_effect = [io.StringIO("{}"), io.StringIO("{}")]
            with patch.object(gate.time, "monotonic", return_value=10):
                client.get("/test", 12)
                token.write_text("synthetic-token-2")
                client.get("/test", 12)
            calls = client.opener.open.call_args_list
            self.assertEqual(calls[0].args[0].get_header("Authorization"), "Bearer synthetic-token-1")
            self.assertEqual(calls[1].args[0].get_header("Authorization"), "Bearer synthetic-token-2")
            self.assertEqual(calls[0].kwargs["timeout"], 2)

    def test_pod_lists_are_paginated(self):
        client = self.client("/synthetic")
        client.get = Mock(side_effect=[
            {"items": [pod(uid="first")], "metadata": {"continue": "next-page"}},
            {"items": [pod(uid="last")], "metadata": {}},
        ])
        self.assertEqual(len(client.pods("app=reader", 100)), 2)
        self.assertIn("continue=next-page", client.get.call_args_list[1].args[0])

    def test_ipv6_api_endpoint(self):
        client = self.client("/synthetic", "fd00::1")
        self.assertEqual(client.base_url, "https://[fd00::1]:443")


if __name__ == "__main__":
    unittest.main()
