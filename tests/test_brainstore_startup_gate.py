import importlib.util
import io
import json
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


TEMPLATE = "a" * 64
OLD_TEMPLATE = "b" * 64
LATER_TEMPLATE = "c" * 64


def target(template=TEMPLATE, replicas=1, min_ready=0, name="reader",
           container="brainstore-reader"):
    return {"deployment": name, "container": container, "template": template,
            "replicas": replicas, "minReadySeconds": min_ready}


def ordering(*targets, job_name="release-migrate-" + "d" * 32):
    return {"data": {"ordering.json": json.dumps({
        "job": job_name, "targets": list(targets) if targets else [target()],
    })}}


def pod(version="v2.15.0", uid="new", is_ready=True, template=TEMPLATE,
        container="brainstore-reader", image=None, declared_version=None, **metadata):
    image = image if image is not None else "registry/brainstore:" + version
    if template is not None:
        metadata.setdefault("annotations", {})["braintrust.dev/pod-template"] = template
    if declared_version is not None:
        metadata.setdefault("annotations", {})["braintrust.dev/brainstore-version"] = declared_version
    return {
        "metadata": {"name": uid, "uid": uid, **metadata},
        "spec": {"containers": [{"name": container, "image": image}]},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True" if is_ready else "False"}],
            "containerStatuses": [{"name": container, "image": image,
                                   "ready": is_ready, "state": {"running": {}}}],
        },
    }


def deployment(version="v2.15.0", replicas=1, template=TEMPLATE, container="brainstore-reader",
               image=None, declared_version=None, min_ready=0):
    return {
        "metadata": {"name": "reader", "uid": "reader-deployment", "generation": 2},
        "spec": {"replicas": replicas, "minReadySeconds": min_ready, "template": {
            "metadata": {"annotations": pod(
                template=template, declared_version=declared_version,
            )["metadata"].get("annotations", {})},
            "spec": pod(version, container=container, image=image)["spec"],
        }},
        "status": {"observedGeneration": 2, "updatedReplicas": replicas,
                   "readyReplicas": replicas, "availableReplicas": replicas, "replicas": replicas},
    }


class CompatibilityTests(unittest.TestCase):
    def check(self, resource=None, pods=None, minimum="2.15.0"):
        return gate.check_deployment(
            resource if resource is not None else deployment(),
            pods if pods is not None else [pod()],
            target(), gate.release_version(minimum),
        )

    def test_new_api_waits_when_helm_has_not_updated_brainstore_spec(self):
        self.assertIsNotNone(self.check(deployment("v2.14.0")))

    def test_new_api_waits_for_incompatible_old_pods(self):
        self.assertIsNotNone(self.check(pods=[pod(), pod("v2.14.0", "old")]))

    def test_incompatible_terminating_pod_blocks_even_when_unready(self):
        old = pod("v2.14.0", "old", False, deletionTimestamp="2026-09-30T12:00:00Z")
        self.assertIsNotNone(self.check(pods=[pod(), old]))

    def test_old_api_can_restart_against_newer_brainstore(self):
        self.assertIsNone(self.check(deployment("v2.16.0"), [pod("v2.16.0")], "2.15.0"))

    def test_unknown_and_other_major_images_fail_closed(self):
        for version in ("latest", "deadbeef", "0123456", "0" * 40,
                        "v3.0.0", "v2.15.0-rc.1"):
            with self.subTest(version=version):
                self.assertIsNotNone(self.check(deployment(version)))
                self.assertIsNotNone(self.check(pods=[pod(version)]))

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
        self.assertIsNotNone(self.check(pods=[wrong]))
        resource = deployment()
        resource["spec"]["template"]["spec"]["containers"][0]["name"] = "sidecar"
        self.assertIsNotNone(self.check(resource))

    def test_runtime_image_aliases_do_not_block_a_compatible_deployment_pod(self):
        for image in ("registry/brainstore:deadbeef", "registry/brainstore@sha256:abc"):
            with self.subTest(image=image):
                current = pod()
                current["status"]["containerStatuses"][0]["image"] = image
                self.assertIsNone(self.check(pods=[current]))

    def test_runtime_alias_does_not_override_an_incompatible_pod_spec(self):
        current = pod("v2.14.0")
        current["status"]["containerStatuses"][0]["image"] = "registry/brainstore:v2.15.0"
        self.assertIsNotNone(self.check(pods=[current]))


class OpaqueImageTests(unittest.TestCase):
    def rollout(self, image="registry/brainstore:deadbeef", declared_version="2.15.0"):
        return (
            deployment(image=image, declared_version=declared_version),
            pod(image=image, declared_version=declared_version),
        )

    def check(self, resource, pods, minimum="2.15.0"):
        return gate.check_deployment(
            resource, pods, target(replicas=resource["spec"]["replicas"]),
            gate.release_version(minimum),
        )

    def test_sha_custom_prerelease_and_digest_pins_accept_explicit_assertions(self):
        for image in (
            "registry/brainstore:deadbeef", "registry/brainstore:" + "a" * 40,
            "registry/brainstore:0123456", "registry/brainstore:" + "0" * 40,
            "registry/brainstore:custom-build", "registry/brainstore:latest",
            "registry/brainstore:v2.15.0-rc.1",
            "localhost:5000/brainstore@sha256:abc",
            "registry/brainstore:deadbeef@sha256:abc",
        ):
            for declared_version in ("2.15.0", "v2.16.0"):
                with self.subTest(image=image, declared_version=declared_version):
                    resource, current = self.rollout(image, declared_version)
                    self.assertIsNone(self.check(resource, [current]))

    def test_missing_or_invalid_template_assertion_blocks_matching_pods(self):
        for declared_version in (None, "", "unknown", "2.015.0", "2.15",
                                 "2.15.0-rc.1", "2.15.0+build", "2.15.0\n", 215, True):
            with self.subTest(declared_version=declared_version):
                resource, current = self.rollout(declared_version=declared_version)
                self.assertIsNotNone(self.check(resource, [current]))

    def test_lower_or_other_major_template_assertions_cannot_be_overridden_by_pods(self):
        for declared_version in ("2.14.99", "1.99.0", "3.0.0"):
            with self.subTest(declared_version=declared_version):
                resource, current = self.rollout(declared_version=declared_version)
                self.assertIsNotNone(self.check(resource, [current]))
                current["metadata"]["annotations"]["braintrust.dev/brainstore-version"] = "2.15.0"
                self.assertIsNotNone(self.check(resource, [current]))

    def test_every_live_pod_must_have_the_exact_template_assertion(self):
        for declared_version in (None, "", "unknown", "2.14.0", "3.0.0",
                                 "2.16.0", "v2.15.0", 215):
            with self.subTest(declared_version=declared_version):
                resource, _ = self.rollout()
                current = pod(image="registry/brainstore:deadbeef",
                              declared_version=declared_version)
                self.assertIsNotNone(self.check(resource, [current]))
                resource["spec"]["replicas"] = 2
                for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
                    resource["status"][counter] = 2
                stray = pod(uid="stray", image="registry/brainstore:deadbeef",
                            declared_version="2.15.0")
                self.assertIsNotNone(self.check(resource, [stray, current]))

    def test_matching_assertion_does_not_allow_a_different_image_or_stray_backend(self):
        for desired_image, other_image in (
            ("registry/brainstore:deadbeef", "registry/brainstore:feedface"),
            ("registry/brainstore:deadbeef", "other/brainstore:deadbeef"),
            ("registry/brainstore:deadbeef", "registry/brainstore:v2.15.0"),
            ("registry/brainstore:deadbeef@sha256:abc", "registry/brainstore:deadbeef"),
            ("registry/brainstore@sha256:abc", "registry/brainstore@sha256:def"),
        ):
            with self.subTest(desired_image=desired_image, other_image=other_image):
                resource, _ = self.rollout(desired_image)
                stray = pod(uid="stray", image=other_image, declared_version="2.15.0")
                self.assertIsNotNone(self.check(resource, [stray]))
                resource["spec"]["replicas"] = 2
                for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
                    resource["status"][counter] = 2
                current = pod(image=desired_image, declared_version="2.15.0")
                self.assertIsNotNone(self.check(resource, [current, stray]))

    def test_assertion_does_not_replace_a_missing_desired_or_pod_container(self):
        resource, current = self.rollout()
        current["spec"]["containers"] = []
        self.assertIsNotNone(self.check(resource, [current]))
        resource, current = self.rollout()
        resource["spec"]["template"]["spec"]["containers"] = []
        self.assertIsNotNone(self.check(resource, [current]))

    def test_forged_assertions_cannot_override_stable_image_versions(self):
        for image in ("registry/brainstore:v2.14.0", "registry/brainstore:v3.0.0",
                      "registry/brainstore:v2.14.0@sha256:abc",
                      "registry/brainstore:v3.0.0@sha256:abc"):
            with self.subTest(image=image):
                resource, current = self.rollout(image)
                self.assertIsNotNone(self.check(resource, [current]))
                compatible_pod = pod(declared_version="2.15.0")
                self.assertIsNotNone(self.check(resource, [compatible_pod]))
                resource = deployment(declared_version="2.15.0")
                self.assertIsNotNone(self.check(resource, [current]))

    def test_stable_images_use_actual_versions_not_assertions(self):
        for declared_version in (None, "invalid", "1.0.0", "3.0.0"):
            with self.subTest(declared_version=declared_version):
                resource, current = self.rollout("registry/brainstore:v2.16.0",
                                                 declared_version)
                self.assertIsNone(self.check(resource, [current]))
        resource = deployment()
        _, opaque = self.rollout()
        self.assertIsNotNone(self.check(resource, [opaque]))

    def test_current_assertion_still_requires_a_completed_observed_rollout(self):
        for change in ("generation", "template", "pod_template", "terminating",
                       "unready", "missing_pod", "surge",
                       "updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            with self.subTest(change=change):
                resource, current = self.rollout()
                pods = [current]
                if change == "generation":
                    resource["status"]["observedGeneration"] = 1
                elif change == "template":
                    resource["spec"]["template"]["metadata"]["annotations"][
                        "braintrust.dev/pod-template"] = OLD_TEMPLATE
                elif change == "pod_template":
                    current["metadata"]["annotations"]["braintrust.dev/pod-template"] = OLD_TEMPLATE
                elif change == "terminating":
                    current["metadata"]["deletionTimestamp"] = "2026-09-30T12:00:00Z"
                elif change == "unready":
                    current["status"]["containerStatuses"][0]["ready"] = False
                elif change == "missing_pod":
                    pods = []
                elif change == "surge":
                    pods.append(pod(uid="surge", image="registry/brainstore:deadbeef",
                                    declared_version="2.15.0"))
                else:
                    resource["status"][change] = 0
                self.assertIsNotNone(self.check(resource, pods))

    def test_older_api_uses_later_opaque_rollouts_own_assertion(self):
        resource, current = self.rollout("registry/brainstore:later-sha", "2.16.0")
        self.assertIsNone(self.check(resource, [current]))
        self.assertIsNotNone(self.check(resource, [current], minimum="2.17.0"))
        current["metadata"]["annotations"]["braintrust.dev/brainstore-version"] = "2.15.0"
        self.assertIsNotNone(self.check(resource, [current]))
        resource, current = self.rollout("registry/brainstore:major-three", "3.0.0")
        self.assertIsNotNone(self.check(resource, [current]))
        self.assertIsNone(self.check(resource, [current], minimum="3.0.0"))


    def test_later_opaque_major_rollout_resets_an_older_apis_confirmation(self):
        first, first_pod = self.rollout()
        later, later_pod = self.rollout("registry/brainstore:major-three", "3.0.0")
        client = api_client()
        client.deployment.side_effect = [first, later, later]
        client.pods.side_effect = [[first_pod], [later_pod], [later_pod]]
        clock = FakeClock()
        with self.assertRaisesRegex(TimeoutError, "declared Brainstore version"):
            gate.wait_for_fleet(
                client, "release-ordering", (2, 15, 0), 45, 15,
                clock.clock, clock.sleep, Mock(),
            )
        self.assertEqual(client.deployment.call_count, 3)

    def test_opaque_zero_role_needs_drain_not_a_compatible_assertion(self):
        for declared_version in (None, "invalid", "1.0.0", "3.0.0"):
            with self.subTest(declared_version=declared_version):
                resource = deployment(replicas=0, image="registry/brainstore@sha256:abc",
                                      declared_version=declared_version)
                self.assertIsNone(self.check(resource, []))
                current = pod(image="registry/brainstore@sha256:abc",
                              declared_version=declared_version)
                self.assertIsNotNone(self.check(resource, [current]))
                resource["status"]["replicas"] = 1
                self.assertIsNotNone(self.check(resource, []))


def migration_job(*conditions):
    return {"status": {"conditions": [
        {"type": condition, "status": "True"} for condition in conditions
    ]}}


def api_client(configs=None, jobs=None):
    configs = iter(configs) if configs is not None else None
    jobs = iter(jobs) if jobs is not None else None

    def get(path, deadline):
        if "/configmaps/" in path:
            resource = next(configs) if configs is not None else ordering()
        elif "/jobs/" in path:
            resource = next(jobs) if jobs is not None else migration_job("Complete")
        else:
            raise AssertionError(f"unexpected API path: {path}")
        if isinstance(resource, BaseException):
            raise resource
        return resource

    client = Mock(namespace="ns")
    client.get.side_effect = get
    return client


class RolloutTests(unittest.TestCase):
    def check(self, resource=None, pods=None, expected=None):
        return gate.check_deployment(
            resource if resource is not None else deployment(),
            pods if pods is not None else [pod()],
            expected if expected is not None else target(), (2, 15, 0),
        )

    def test_same_image_content_change_waits_for_current_pods(self):
        old = pod(template=OLD_TEMPLATE)
        self.assertIsNotNone(self.check(pods=[old, pod(is_ready=False)]))
        self.assertIsNotNone(self.check(pods=[old]))
        self.assertIsNone(self.check(pods=[pod()]))

    def test_env_change_waits_for_updated_replicas(self):
        resource = deployment()
        resource["metadata"]["generation"] = 3
        resource["status"]["observedGeneration"] = 3
        resource["spec"]["template"]["spec"]["containers"][0]["env"] = [
            {"name": "BRAINSTORE_CONFIG", "value": "new-config"},
        ]
        resource["status"]["updatedReplicas"] = 0
        self.assertIsNotNone(self.check(resource))
        resource["status"]["updatedReplicas"] = 1
        current = pod()
        current["spec"] = resource["spec"]["template"]["spec"]
        self.assertIsNone(self.check(resource, [current]))

    def test_old_complete_generation_cannot_pass_before_helm_updates_content(self):
        self.assertIsNotNone(self.check(deployment(template=OLD_TEMPLATE),
                                        [pod(template=OLD_TEMPLATE)]))

    def test_exact_expected_content_is_required_on_deployment_and_live_pods(self):
        for template in (None, "", "invalid", "a" * 63, OLD_TEMPLATE, LATER_TEMPLATE):
            with self.subTest(template=template):
                self.assertIsNotNone(self.check(deployment(template=template)))
                self.assertIsNotNone(self.check(pods=[pod(template=template)]))
        self.assertIsNone(self.check(deployment(template=LATER_TEMPLATE),
                                     [pod(template=LATER_TEMPLATE)],
                                     target(template=LATER_TEMPLATE)))

    def test_expected_replica_count_blocks_stale_healthy_deployment(self):
        for replicas in (0, 2):
            with self.subTest(replicas=replicas):
                current = deployment(replicas=replicas)
                pods = [pod(uid=str(index)) for index in range(replicas)]
                self.assertIsNotNone(self.check(current, pods))
                self.assertIsNone(self.check(current, pods, target(replicas=replicas)))
        self.assertIsNotNone(self.check(expected=target(replicas=0)))

    def test_expected_min_ready_seconds_blocks_stale_healthy_deployment(self):
        self.assertIsNotNone(self.check(expected=target(min_ready=60)))
        self.assertIsNone(self.check(deployment(min_ready=60), [pod()], target(min_ready=60)))
        self.assertIsNotNone(self.check(deployment(min_ready=60)))

    def test_malformed_deployment_counts_fail_closed(self):
        for field in ("replicas", "minReadySeconds"):
            for value in (True, False, None, -1, "1", 1.0):
                with self.subTest(field=field, value=value):
                    resource = deployment()
                    resource["spec"][field] = value
                    self.assertIsNotNone(self.check(resource))

    def test_unobserved_or_invalid_generation_does_not_pass(self):
        for generation in (None, 0, -1, True, "2", 2.0):
            with self.subTest(generation=generation):
                resource = deployment()
                resource["metadata"]["generation"] = generation
                self.assertIsNotNone(self.check(resource))
        for observed in (None, 0, 1, -1, True, "2", 2.0):
            with self.subTest(observed=observed):
                resource = deployment()
                resource["status"]["observedGeneration"] = observed
                self.assertIsNotNone(self.check(resource))

    def test_deleted_deployment_does_not_pass(self):
        resource = deployment()
        resource["metadata"]["deletionTimestamp"] = "2026-09-30T12:00:00Z"
        self.assertIsNotNone(self.check(resource))

    def test_every_replica_counter_must_equal_expected_count(self):
        for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            for count in (None, 0, 2, True, False, "1", 1.0, -1):
                with self.subTest(counter=counter, count=count):
                    resource = deployment()
                    if count is None:
                        del resource["status"][counter]
                    else:
                        resource["status"][counter] = count
                    self.assertIsNotNone(self.check(resource))

    def test_requires_exactly_desired_live_ready_current_pods(self):
        for pods in ([], [pod(is_ready=False)], [pod(), pod(uid="surge")],
                     [pod(deletionTimestamp="2026-09-30T12:00:00Z")],
                     [pod(), pod(uid="terminating", is_ready=False,
                                 deletionTimestamp="2026-09-30T12:00:00Z")]):
            with self.subTest(pods=pods):
                self.assertIsNotNone(self.check(pods=pods))
        for change in ("missing", "not_running", "not_ready", "pod_not_ready", "pending"):
            with self.subTest(change=change):
                current = pod()
                status = current["status"]
                if change == "missing":
                    status["containerStatuses"] = []
                elif change == "not_running":
                    status["containerStatuses"][0]["state"] = {"waiting": {}}
                elif change == "not_ready":
                    status["containerStatuses"][0]["ready"] = False
                elif change == "pod_not_ready":
                    status["conditions"] = []
                else:
                    status["phase"] = "Pending"
                self.assertIsNotNone(self.check(pods=[current]))

    def test_stray_service_backend_also_requires_expected_content(self):
        stray = pod(uid="stray", template=OLD_TEMPLATE)
        self.assertIsNotNone(self.check(pods=[stray]))
        del stray["metadata"]["annotations"]
        self.assertIsNotNone(self.check(pods=[stray]))

    def test_terminal_pods_do_not_block(self):
        for phase in ("Succeeded", "Failed"):
            with self.subTest(phase=phase):
                old = pod("v2.14.0", template=OLD_TEMPLATE)
                old["status"]["phase"] = phase
                self.assertIsNone(self.check(pods=[pod(), old]))

    def test_honors_deployment_min_ready_seconds_availability(self):
        resource = deployment(min_ready=60)
        resource["status"]["availableReplicas"] = 0
        self.assertIsNotNone(self.check(resource, expected=target(min_ready=60)))
        resource["status"]["availableReplicas"] = 1
        self.assertIsNone(self.check(resource, expected=target(min_ready=60)))

    def test_zero_role_requires_expected_content_and_completed_scale_down(self):
        expected = target(replicas=0)
        resource = deployment(replicas=0)
        self.assertIsNone(self.check(resource, [], expected))
        self.assertIsNotNone(self.check(resource, [pod()], expected))
        for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
            for count in (1, -1, True, False, "0", 0.0):
                with self.subTest(counter=counter, count=count):
                    resource["status"][counter] = count
                    self.assertIsNotNone(self.check(resource, [], expected))
            del resource["status"][counter]
        self.assertIsNone(self.check(resource, [], expected))
        for template in (None, OLD_TEMPLATE):
            with self.subTest(template=template):
                self.assertIsNotNone(self.check(
                    deployment(replicas=0, template=template), [], expected,
                ))
        resource["status"]["observedGeneration"] = 1
        self.assertIsNotNone(self.check(resource, [], expected))

    def test_zero_role_does_not_require_an_inactive_image_or_container(self):
        expected = target(replicas=0)
        for version in ("v2.14.0", "v3.0.0", "deadbeef", "v2.15.0-rc.1"):
            with self.subTest(version=version):
                self.assertIsNone(self.check(deployment(version, replicas=0), [], expected))
        resource = deployment(replicas=0)
        resource["spec"]["template"]["spec"]["containers"] = []
        self.assertIsNone(self.check(resource, [], expected))
        terminating = pod(is_ready=False, deletionTimestamp="2026-09-30T12:00:00Z")
        self.assertIsNotNone(self.check(resource, [terminating], expected))
        for phase in ("Succeeded", "Failed"):
            with self.subTest(phase=phase):
                terminal = pod("v2.14.0", template=OLD_TEMPLATE)
                terminal["status"]["phase"] = phase
                self.assertIsNone(self.check(resource, [terminal], expected))

    def test_older_application_accepts_latest_expected_compatible_rollout_only(self):
        resource = deployment("v2.16.0", template=LATER_TEMPLATE)
        expected = target(template=LATER_TEMPLATE)
        later = pod("v2.16.0", template=LATER_TEMPLATE)
        self.assertIsNone(self.check(resource, [later], expected))
        self.assertIsNotNone(self.check(resource, [pod()], expected))
        resource["status"]["updatedReplicas"] = 0
        self.assertIsNotNone(self.check(resource, [later], expected))


class OrderingConfigTests(unittest.TestCase):
    def load(self, resource):
        client = Mock(namespace="custom%20namespace")
        client.get.return_value = resource
        return gate.load_ordering_config(client, "release-ordering", 100)

    def test_loads_latest_config_from_api_and_preserves_zero_targets(self):
        client = Mock(namespace="custom%20namespace")
        client.get.side_effect = [ordering(), ordering(target(replicas=0, min_ready=60))]
        first = gate.load_ordering_config(client, "release/ordering", 100)
        second = gate.load_ordering_config(client, "release/ordering", 100)
        self.assertEqual(first["targets"], [target()])
        self.assertEqual(second["targets"], [target(replicas=0, min_ready=60)])
        self.assertEqual([call.args for call in client.get.call_args_list], [
            ("/api/v1/namespaces/custom%20namespace/configmaps/release%2Fordering", 100),
        ] * 2)

    def test_missing_or_malformed_configmap_data_fails_closed(self):
        for resource in (None, [], {}, {"data": None}, {"data": []}, {"data": {}},
                         {"data": {"ordering.json": None}},
                         {"data": {"ordering.json": ""}},
                         {"data": {"ordering.json": "not JSON"}}):
            with self.subTest(resource=resource), self.assertRaises((KeyError, TypeError, ValueError)):
                self.load(resource)

    def test_job_and_nonempty_target_list_are_required(self):
        for config in (None, [], {}, {"job": "job"}, {"targets": [target()]},
                       {"job": "job", "targets": []},
                       {"job": "job", "targets": {}},
                       {"job": "job", "targets": [None]},
                       {"job": "job", "targets": [{}]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.load({"data": {"ordering.json": json.dumps(config)}})
        for job_name in (None, "", True, 123, [], {}):
            with self.subTest(job_name=job_name), self.assertRaises(ValueError):
                self.load(ordering(job_name=job_name))

    def test_every_target_requires_names_and_sha256_content(self):
        invalid = {
            "deployment": (None, "", True, 1, [], {}),
            "container": (None, "", True, 1, [], {}),
            "template": (None, "", True, 1, "a" * 63, "a" * 65, "A" * 64, "z" * 64,
                         TEMPLATE + "\n"),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    current = target()
                    current[field] = value
                    self.load(ordering(target(), current))
        for field in target():
            with self.subTest(missing=field), self.assertRaises(ValueError):
                current = target()
                del current[field]
                self.load(ordering(current))

    def test_replica_and_min_ready_shapes_are_nonnegative_integers_not_booleans(self):
        for field in ("replicas", "minReadySeconds"):
            for value in (None, -1, True, False, 1.0, "1", [], {}):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    current = target()
                    current[field] = value
                    self.load(ordering(current))
        self.assertEqual(self.load(ordering(target(replicas=0, min_ready=0)))["targets"],
                         [target(replicas=0, min_ready=0)])


class FakeClock:
    def __init__(self):
        self.now = 0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class WaitTests(unittest.TestCase):
    def wait(self, observations, timeout=60, configs=None, jobs=None):
        clock = FakeClock()
        log = Mock()
        client = api_client(configs, jobs)
        with patch.object(gate, "check_fleet", side_effect=observations) as check:
            gate.wait_for_fleet(client, "release-ordering", (2, 15, 0), timeout, 15,
                                clock.clock, clock.sleep, log)
        return clock, log, check, client

    def test_requires_two_successful_observations_and_reads_config_and_job_each_time(self):
        clock, _, check, client = self.wait([None, None])
        self.assertEqual(check.call_count, 2)
        self.assertEqual(client.get.call_count, 4)
        self.assertEqual(clock.now, 15)

    def test_failed_check_resets_confirmation(self):
        _, _, check, _ = self.wait([None, "old pod remains", None, None])
        self.assertEqual(check.call_count, 4)

    def test_every_expected_state_change_resets_confirmation(self):
        for changed in (ordering(target(template=LATER_TEMPLATE)), ordering(target(replicas=2)),
                        ordering(target(min_ready=60)), ordering(target(name="new-reader")),
                        ordering(job_name="new-job")):
            with self.subTest(changed=changed):
                clock, _, check, _ = self.wait([None, None, None],
                                               configs=[ordering(), changed, changed])
                self.assertEqual(check.call_count, 3)
                self.assertEqual(clock.now, 30)

    def test_transient_missing_deployment_and_rbac_are_retried(self):
        errors = [urllib.error.HTTPError("https://api", code, "synthetic", {}, io.BytesIO())
                  for code in (404, 403)]
        _, _, check, _ = self.wait(errors + [None, None])
        self.assertEqual(check.call_count, 4)

    def test_failed_api_reads_reset_successful_observations(self):
        for error in (OSError("synthetic"), ValueError("synthetic"),
                      KeyError("synthetic"), TypeError("synthetic")):
            with self.subTest(error=type(error).__name__):
                clock, _, check, _ = self.wait([None, error, None, None])
                self.assertEqual(check.call_count, 4)
                self.assertEqual(clock.now, 45)

    def test_invalid_config_resets_confirmation_without_observing_deployments(self):
        clock, _, check, client = self.wait(
            [None, None, None], configs=[ordering(), {"data": {}}, ordering(), ordering()],
        )
        self.assertEqual(client.get.call_count, 7)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(clock.now, 45)

    def test_current_migration_job_must_be_complete_on_every_observation(self):
        clock, _, check, client = self.wait(
            [None, None, None], timeout=75,
            configs=[ordering(), ordering(job_name="new-job"),
                     ordering(job_name="new-job"), ordering(job_name="new-job")],
            jobs=[migration_job("Complete"), migration_job(),
                  migration_job("Complete"), migration_job("Complete")],
        )
        self.assertEqual(check.call_count, 3)
        self.assertEqual(clock.now, 45)
        job_paths = [call.args[0] for call in client.get.call_args_list if "/jobs/" in call.args[0]]
        self.assertEqual(job_paths[1:], ["/apis/batch/v1/namespaces/ns/jobs/new-job"] * 3)

    def test_pending_missing_or_failed_migration_never_releases_api(self):
        for observation in (migration_job(),
                            urllib.error.HTTPError("https://api/jobs/job", 404, "", {}, io.BytesIO())):
            with self.subTest(observation=observation), self.assertRaises(TimeoutError):
                self.wait([], timeout=30, jobs=[observation, observation])
        with self.assertRaises(RuntimeError):
            self.wait([], jobs=[migration_job("Failed", "Complete")])

    def test_timeout_never_releases_api(self):
        with self.assertRaisesRegex(TimeoutError, "old pod remains"):
            self.wait(["old pod remains"] * 3, timeout=30)

    def test_kubernetes_api_failure_never_releases_api(self):
        with self.assertRaisesRegex(TimeoutError, "HTTP 429"):
            self.wait([urllib.error.HTTPError("https://api", 429, "synthetic", {}, io.BytesIO())] * 3, 30)

    def test_missing_or_malformed_ordering_config_never_releases_api(self):
        for resource in ({}, ordering(target(template="invalid")), ordering(job_name=""),
                         urllib.error.HTTPError("https://api/configmaps/gate", 404, "", {}, io.BytesIO())):
            with self.subTest(resource=resource), self.assertRaises(TimeoutError):
                self.wait([], timeout=30, configs=[resource, resource])


class FleetTests(unittest.TestCase):
    def test_latest_compatible_content_requires_two_matching_observations(self):
        client = api_client([ordering(), ordering(target(template=LATER_TEMPLATE)),
                             ordering(target(template=LATER_TEMPLATE))])
        first = deployment("v2.15.0")
        later = deployment("v2.16.0", template=LATER_TEMPLATE)
        later["metadata"]["generation"] = 3
        later["status"]["observedGeneration"] = 3
        client.deployment.side_effect = [first, later, later]
        client.pods.side_effect = [[pod("v2.15.0", "old")],
                                   [pod("v2.16.0", "new", template=LATER_TEMPLATE)],
                                   [pod("v2.16.0", "new", template=LATER_TEMPLATE)]]
        clock = FakeClock()
        gate.wait_for_fleet(client, "release-ordering", (2, 15, 0), 60, 15,
                            clock.clock, clock.sleep, Mock())
        self.assertEqual(client.deployment.call_count, 3)
        self.assertEqual(clock.now, 30)

    def test_checks_all_roles_and_includes_stray_service_backends(self):
        client = Mock()
        targets = []
        deployments = []
        pod_sets = []
        for role in ("reader", "writer", "fastreader", "automationwriter"):
            name = "custom-" + role
            container = "brainstore-" + role
            targets.append(target(name=name, container=container))
            resource = deployment(container=container)
            resource["metadata"]["name"] = name
            resource["metadata"]["uid"] = name
            deployments.append(resource)
            pod_sets.append([pod(uid=role, container=container)])
        client.deployment.side_effect = deployments
        client.pods.side_effect = pod_sets
        self.assertIsNone(gate.check_fleet(client, targets, (2, 15, 0), 100))
        self.assertEqual(client.deployment.call_count, 4)
        self.assertEqual([call.args[0] for call in client.pods.call_args_list],
                         ["app=custom-reader", "app=custom-writer",
                          "app=custom-fastreader", "app=custom-automationwriter"])
        self.assertTrue(all(call.args[1] == 100 for call in client.deployment.call_args_list))
        self.assertTrue(all(call.args[1] == 100 for call in client.pods.call_args_list))
        # Service-labelled backends are not exempt based on owner references.
        client.deployment.side_effect = deployments
        client.pods.side_effect = [[pod(), pod("v2.14.0", "stray")]]
        self.assertIsNotNone(gate.check_fleet(client, targets, (2, 15, 0), 100))

    def test_each_role_uses_its_own_expected_counts_and_zero_role_must_drain(self):
        client = Mock()
        targets = [target(replicas=2, min_ready=60),
                   target(replicas=0, name="automationwriter",
                          container="brainstore-automationwriter")]
        reader = deployment(replicas=2, min_ready=60)
        writer = deployment(replicas=0, container="brainstore-automationwriter")
        writer["metadata"]["name"] = "automationwriter"
        client.deployment.side_effect = [reader, writer]
        client.pods.side_effect = [[pod(), pod(uid="second")], []]
        self.assertIsNone(gate.check_fleet(client, targets, (2, 15, 0), 100))
        client.deployment.side_effect = [reader, writer]
        client.pods.side_effect = [[pod(), pod(uid="second")],
                                   [pod(container="brainstore-automationwriter")]]
        self.assertIsNotNone(gate.check_fleet(client, targets, (2, 15, 0), 100))

    def test_writer_waits_across_same_image_and_later_rollouts(self):
        container = "brainstore-writer"
        templates = (OLD_TEMPLATE, TEMPLATE, TEMPLATE,
                     LATER_TEMPLATE, LATER_TEMPLATE, LATER_TEMPLATE)
        resources = [deployment(template=template, container=container) for template in templates]
        for resource in resources:
            resource["metadata"]["name"] = "writer"
        resources[1]["status"]["updatedReplicas"] = 0
        resources[3]["status"]["updatedReplicas"] = 0
        old = pod(template=OLD_TEMPLATE, container=container)
        current = pod(container=container)
        later = pod(template=LATER_TEMPLATE, container=container)
        initial = ordering(target(name="writer", container=container))
        latest = ordering(target(template=LATER_TEMPLATE, name="writer", container=container))
        client = api_client([initial, initial, initial, latest, latest, latest])
        client.deployment.side_effect = resources
        client.pods.side_effect = [
            [old], [old, pod(is_ready=False, container=container)],
            [current], [current, pod(template=LATER_TEMPLATE, is_ready=False, container=container)],
            [later], [later],
        ]
        clock = FakeClock()
        gate.wait_for_fleet(client, "release-ordering", (2, 15, 0), 120, 15,
                            clock.clock, clock.sleep, Mock())
        self.assertEqual(clock.now, 75)

    def test_new_config_does_not_accept_old_healthy_deployment_before_helm_update(self):
        for expected in (target(template=LATER_TEMPLATE), target(replicas=2),
                         target(replicas=0), target(min_ready=60)):
            with self.subTest(expected=expected):
                client = api_client([ordering(expected), ordering(expected)])
                client.deployment.return_value = deployment()
                client.pods.return_value = [pod()]
                clock = FakeClock()
                with self.assertRaises(TimeoutError):
                    gate.wait_for_fleet(client, "release-ordering", (2, 15, 0), 30, 15,
                                        clock.clock, clock.sleep, Mock())

    def test_completed_observation_cannot_release_application_after_deadline(self):
        clock = FakeClock()

        def observe(name, deadline):
            clock.sleep(30)
            return deployment()

        client = api_client()
        client.deployment.side_effect = observe
        client.pods.return_value = [pod()]
        with self.assertRaises(TimeoutError):
            gate.wait_for_fleet(client, "release-ordering", (2, 15, 0), 60, 15,
                                clock.clock, clock.sleep, Mock())


class MainTests(unittest.TestCase):
    def environment(self):
        return {
            "BRAINSTORE_MINIMUM_VERSION": "2.15.0",
            "ORDERING_CONFIGMAP": "release-ordering",
            "BRAINSTORE_GATE_TIMEOUT_SECONDS": "60",
            "BRAINSTORE_GATE_POLL_SECONDS": "15",
            "POD_NAMESPACE": "custom",
        }

    def test_uses_stable_configmap_and_fixed_minimum_version(self):
        with patch.dict(os.environ, self.environment(), clear=True), \
             patch.object(gate, "KubernetesClient") as client, \
             patch.object(gate, "wait_for_fleet") as wait:
            gate.main()
        client.assert_called_once_with("custom", "/var/run/brainstore-gate")
        wait.assert_called_once_with(client.return_value, "release-ordering", (2, 15, 0), 60, 15)

    def test_missing_or_invalid_environment_fails_before_polling(self):
        invalid = {
            "ORDERING_CONFIGMAP": (None, ""),
            "BRAINSTORE_MINIMUM_VERSION": (None, "", "latest", "2.15.0-rc.1"),
            "BRAINSTORE_GATE_TIMEOUT_SECONDS": (None, "", "invalid", "0", "-1", "15"),
            "BRAINSTORE_GATE_POLL_SECONDS": (None, "", "invalid", "0", "-1", "60"),
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
                         patch.object(gate, "wait_for_fleet") as wait, \
                         self.assertRaises((KeyError, ValueError)):
                        gate.main()
                    wait.assert_not_called()


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

    def test_expired_budget_does_not_start_an_api_request(self):
        client = self.client("/synthetic")
        client.opener = Mock()
        with patch.object(gate.time, "monotonic", return_value=10), \
             self.assertRaises(TimeoutError):
            client.get("/test", 10)
        client.opener.open.assert_not_called()

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
