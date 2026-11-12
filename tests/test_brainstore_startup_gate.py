import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error


SCRIPT = Path(__file__).resolve().parents[1] / "braintrust/files/brainstore-startup-gate.py"
spec = importlib.util.spec_from_file_location("gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def pod(version="v2.15.0", uid="new", is_ready=True, revision=None,
        container="brainstore-reader", **metadata):
    if revision is not None:
        metadata["annotations"] = {"braintrust.dev/release-revision": str(revision)}
    return {
        "metadata": {"name": uid, "uid": uid, **metadata},
        "spec": {"containers": [{"name": container, "image": "registry/brainstore:" + version}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True" if is_ready else "False"}],
            "containerStatuses": [{"name": container, "image": "registry/brainstore:" + version,
                                   "ready": is_ready, "state": {"running": {}}}],
        },
    }


def deployment(version="v2.15.0", replicas=1, revision=None, container="brainstore-reader"):
    return {
        "metadata": {"name": "reader", "uid": "reader-deployment", "generation": 2},
        "spec": {"replicas": replicas, "template": {
            "metadata": {"annotations": pod(revision=revision)["metadata"].get("annotations", {})},
            "spec": pod(version, container=container)["spec"],
        }},
        "status": {"observedGeneration": 2, "updatedReplicas": replicas,
                   "readyReplicas": replicas, "availableReplicas": replicas, "replicas": replicas},
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


class StrictRolloutTests(unittest.TestCase):
    def check(self, resource=None, pods=None, minimum_revision=8):
        return gate.check_deployment(
            resource if resource is not None else deployment(revision=8),
            pods if pods is not None else [pod(revision=8)],
            "brainstore-reader", (2, 15, 0), minimum_revision,
        )

    def test_same_image_release_waits_for_current_pods(self):
        old = pod(revision=7)
        current = pod(revision=8, is_ready=False)
        self.assertIsNotNone(self.check(pods=[old, current]))
        self.assertIsNotNone(self.check(pods=[old]))
        current = pod(revision=8)
        self.assertIsNone(self.check(pods=[current]))

    def test_same_release_env_change_waits_for_updated_replicas(self):
        resource = deployment(revision=8)
        resource["metadata"]["generation"] = 3
        resource["status"]["observedGeneration"] = 3
        resource["spec"]["template"]["spec"]["containers"][0]["env"] = [
            {"name": "BRAINSTORE_CONFIG", "value": "new-config"},
        ]
        resource["status"]["updatedReplicas"] = 0
        self.assertIsNotNone(self.check(resource))
        resource["status"]["updatedReplicas"] = 1
        current = pod(revision=8)
        current["spec"] = resource["spec"]["template"]["spec"]
        self.assertIsNone(self.check(resource, [current]))

    def test_old_complete_generation_cannot_pass_before_helm_update(self):
        self.assertIsNotNone(self.check(deployment(revision=7), [pod(revision=7)]))

    def test_revision_floor_rejects_missing_malformed_and_old_markers(self):
        for revision in (None, "", "invalid", "8.0", 7):
            with self.subTest(revision=revision):
                self.assertIsNotNone(self.check(deployment(revision=revision)))

    def test_requires_current_controller_observation(self):
        resource = deployment(revision=8)
        resource["status"]["observedGeneration"] = 1
        self.assertIsNotNone(self.check(resource))

    def test_every_replica_counter_must_equal_current_desired_count(self):
        for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            for count in (None, 0, 2):
                with self.subTest(counter=counter, count=count):
                    resource = deployment(revision=8)
                    if count is None:
                        del resource["status"][counter]
                    else:
                        resource["status"][counter] = count
                    self.assertIsNotNone(self.check(resource))

    def test_requires_exactly_desired_live_ready_current_pods(self):
        for pods in ([], [pod(revision=8, is_ready=False)],
                     [pod(revision=8), pod(uid="surge", revision=8)],
                     [pod(revision=8, deletionTimestamp="2026-09-30T12:00:00Z")],
                     [pod(revision=8), pod(uid="terminating", revision=8, is_ready=False,
                                           deletionTimestamp="2026-09-30T12:00:00Z")]):
            with self.subTest(pods=pods):
                self.assertIsNotNone(self.check(pods=pods))
        current = pod(revision=8)
        current["status"]["containerStatuses"] = []
        self.assertIsNotNone(self.check(pods=[current]))

    def test_stray_service_backend_is_not_exempt_from_revision_check(self):
        stray = pod(uid="stray", revision=7)
        self.assertIsNotNone(self.check(pods=[stray]))
        del stray["metadata"]["annotations"]
        self.assertIsNotNone(self.check(pods=[stray]))

    def test_terminal_old_pods_do_not_block_completed_rollout(self):
        for phase in ("Succeeded", "Failed"):
            with self.subTest(phase=phase):
                old = pod("v2.14.0", revision=7)
                old["status"]["phase"] = phase
                self.assertIsNone(self.check(pods=[pod(revision=8), old]))

    def test_min_ready_seconds_requires_available_replicas(self):
        resource = deployment(revision=8)
        resource["spec"]["minReadySeconds"] = 60
        resource["status"]["availableReplicas"] = 0
        self.assertIsNotNone(self.check(resource))
        resource["status"]["availableReplicas"] = 1
        self.assertIsNone(self.check(resource))

    def test_zero_role_requires_release_identity_and_completed_scale_down(self):
        resource = deployment(replicas=0, revision=8)
        self.assertIsNone(self.check(resource, []))
        self.assertIsNotNone(self.check(resource, [pod(revision=8)]))
        for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            with self.subTest(counter=counter):
                resource["status"][counter] = 1
                self.assertIsNotNone(self.check(resource, []))
                del resource["status"][counter]
        self.assertIsNone(self.check(resource, []))
        for revision in (None, 7):
            with self.subTest(revision=revision):
                self.assertIsNotNone(self.check(deployment(replicas=0, revision=revision), []))
        resource["status"]["observedGeneration"] = 1
        self.assertIsNotNone(self.check(resource, []))

    def test_uses_current_replica_count_after_scaling(self):
        resource = deployment(replicas=2, revision=8)
        self.assertIsNotNone(self.check(resource, [pod(revision=8)]))
        self.assertIsNone(self.check(resource, [pod(revision=8), pod(uid="second", revision=8)]))

    def test_older_api_accepts_later_completed_but_not_unfinished_rollout(self):
        resource = deployment("v2.16.0", revision=9)
        self.assertIsNone(self.check(resource, [pod("v2.16.0", revision=9)]))
        self.assertIsNotNone(self.check(resource, [pod(revision=8)]))
        resource["status"]["updatedReplicas"] = 0
        self.assertIsNotNone(self.check(resource, [pod("v2.16.0", revision=9)]))

    def test_strict_mode_preserves_desired_and_pod_image_compatibility(self):
        for version in ("v2.14.0", "v3.0.0", "latest", "v2.15.0-rc.1"):
            with self.subTest(version=version):
                self.assertIsNotNone(self.check(deployment(version, revision=8)))
                self.assertIsNotNone(self.check(pods=[pod(version, revision=8)]))


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


    def test_strict_writer_waits_across_same_image_and_later_rollouts(self):
        container = "brainstore-writer"
        resources = [deployment(revision=revision, container=container)
                     for revision in (7, 8, 8, 9, 9, 9)]
        for resource in resources:
            resource["metadata"]["name"] = "writer"
        resources[1]["status"]["updatedReplicas"] = 0
        resources[3]["status"]["updatedReplicas"] = 0
        old = pod(revision=7, container=container)
        current = pod(revision=8, container=container)
        later = pod(revision=9, container=container)
        client = Mock()
        client.deployment.side_effect = resources
        client.pods.side_effect = [
            [old], [old, pod(revision=8, is_ready=False, container=container)],
            [current], [current, pod(revision=9, is_ready=False, container=container)],
            [later], [later],
        ]
        targets = [{"deployment": "writer", "container": container, "minimumRevision": 8}]
        clock = FakeClock()
        gate.wait_for_fleet(client, targets, (2, 15, 0), 120, 15,
                            clock.clock, clock.sleep, Mock())
        self.assertEqual(clock.now, 75)

    def test_strict_observation_errors_reset_confirmation(self):
        client = Mock()
        resource = deployment(revision=8)
        client.deployment.side_effect = [
            urllib.error.HTTPError("https://api", 403, "synthetic", None, None),
            resource, OSError("synthetic"), resource, resource,
        ]
        client.pods.return_value = [pod(revision=8)]
        targets = [{"deployment": "reader", "container": "brainstore-reader", "minimumRevision": 8}]
        clock = FakeClock()
        gate.wait_for_fleet(client, targets, (2, 15, 0), 90, 15,
                            clock.clock, clock.sleep, Mock())
        self.assertEqual(clock.now, 60)

    def test_strict_completed_observation_cannot_release_api_after_deadline(self):
        clock = FakeClock()

        def observe(name, deadline):
            clock.sleep(30)
            return deployment(revision=8)

        client = Mock()
        client.deployment.side_effect = observe
        client.pods.return_value = [pod(revision=8)]
        targets = [{"deployment": "reader", "container": "brainstore-reader", "minimumRevision": 8}]
        with self.assertRaises(TimeoutError):
            gate.wait_for_fleet(client, targets, (2, 15, 0), 60, 15,
                                clock.clock, clock.sleep, Mock())


class ClientTests(unittest.TestCase):
    def client(self, directory, host="10.0.0.1"):
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": host}), \
             patch.object(gate.ssl, "create_default_context"):
            return gate.KubernetesClient("custom namespace", directory)

    def test_redirects_never_forward_the_bearer_token(self):
        client = self.client("/synthetic")
        handler = next(handler for handler in client.opener.handlers
                       if isinstance(handler, gate.urllib.request.HTTPRedirectHandler))
        handler.parent = Mock()
        request = gate.urllib.request.Request(
            "https://10.0.0.1/api/v1/pods", headers={"Authorization": "Bearer synthetic-token"})
        for code in (301, 302, 303, 307, 308):
            for destination in ("https://other.example/pods", "http://other.example/pods"):
                with self.subTest(code=code, destination=destination), \
                     self.assertRaises(urllib.error.HTTPError) as error:
                    handler.http_error_302(request, io.StringIO(""), code, "Redirect",
                                           {"location": destination})
                self.assertEqual(error.exception.code, code)
                error.exception.close()
        handler.parent.open.assert_not_called()

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
