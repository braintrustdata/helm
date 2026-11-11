# Release ordering: migrations, Brainstore, then API

The default startup order is:

1. A revision-specific migration Job runs the shared API image's migration entry point.
2. New Brainstore application containers wait for that Job, then roll out.
3. A separate revision-specific rollout Job verifies the exact reader/writer configuration is fully deployed.
4. New API application containers wait for the rollout Job, then run the existing live version-compatibility check.

Two Jobs are necessary: if a single Job waited for Brainstore before reporting migration success, Brainstore would wait for that same Job forever. Both phases share one read-only Job-wait helper and revision-scoped code/RBAC. Gateway and Loop runtime retain their migration-only wait.

## Input matrix and intended behavior

| Input | Behavior |
| --- | --- |
| Migrations and API Brainstore startup gate enabled (default), missing/null maps, partial reused values | Migration Job followed by exact Brainstore rollout Job; API waits for rollout success and live compatibility |
| `migrations.enabled: false`, startup gate enabled | No release Jobs; existing live version-compatibility check and API-startup migration behavior |
| Migrations enabled, `api.brainstoreStartupGate.enabled: false` | Migration Job only; all new application containers wait for migrations, without exact Brainstore rollout ordering |
| Both disabled | No chart-managed startup barriers |
| Disabled to enabled | New revision Jobs and pod gates are created |
| Same image with changed inline env or ConfigMap-backed env | Old Ready pods cannot satisfy the new release's rollout barrier |
| API or Brainstore release tags, custom tags, numeric/full/short SHAs, prerelease, tagged digest | Exact rollout identity does not parse versions; the separate API compatibility check still needs a stable minimum for non-release tags |
| AWS, Google, Azure; provider examples | Migrator retains shared API identity, secrets, CA mounts, resources, and scheduling; rollout observer has no cloud identity or secret access |
| Azure Key Vault CSI enabled | Migration Job mounts CSI secrets itself before application containers start |
| API workload isolation/custom pool identities | Every API pool waits for the same rollout Job using its own read-only Job permission |
| Reader, writer, fastreader, rendered automationwriter | Every rendered role participates, including disabled roles that must finish scaling down |
| Missing/null replica count, explicit zero | Missing/null uses Kubernetes' default of one; zero stays zero and requires all non-terminal role pods to disappear |
| Automationwriter absent/null or partial reused map | Absent/null stays omitted; a partial map uses existing automationwriter defaults and participates if rendered |
| Old Deployment still fully Ready before Helm updates it | Expected release revision, image, and ConfigMap checksum must match before rollout state can count |
| Controller not caught up, pending replacements, insufficient Ready replicas, `minReadySeconds`, old/terminating/stray service backends | Barrier remains closed; existing healthy replicas do not stand in for updated replicas |
| Missing/null timing; empty/zero timing or helper image | Existing default/coalescing rules apply; invalid enabled settings fail rendering, unused disabled settings are not validated |
| Migration or rollout failure, missing Job, transient Kubernetes read/RBAC errors | Gates fail closed with bounded waits; completed Jobs are never inferred from absence |
| Subsequent upgrade, superseded API replacement | Each revision has distinct Jobs; old API replacements use their retained rollout success, not an obsolete desired configuration |
| Rollback | Cleanup resets both target-revision Jobs before removing their config/RBAC; migrations and exact rollout verification run again |
| Global/API/role labels and custom pod annotations | Inputs are not mutated or leaked between workloads; observer/migrator pods cannot match API service selectors; managed rollout identity/checksum cannot be overridden |

## Exact rollout contract

The chart stamps each rendered Brainstore Pod template with `braintrust.dev/release-revision`. Each Helm revision already changes its migration Job dependency, forcing replacement even for same-image changes. The rollout Job snapshots the expected deployment/container names, release revision, exact image string, and rendered role ConfigMap checksum.

For each role it checks the Deployment template against those expectations, requires the controller to have observed the desired generation, and waits for all desired replicas to be updated, Ready, and Available. It also lists every service-selected pod, including stray pods, and rejects previous revisions/configurations and terminating live pods. Two successful fleet observations are required within one deadline; this is an observation barrier, not a lock on later changes.

The existing live compatibility check remains intentionally separate. An older API can restart while a later compatible Brainstore release rolls out: its retained rollout Job records the completed prerequisite, while the compatibility check protects its current live connections. Do not make older API replacements wait for their old deployment revision to become current again.

## Configuration and availability limits

This feature requires real Helm install/upgrade revisions. Repeated `helm template | kubectl apply` does not advance revision identity and is unsupported.

The guarantee is **application-container startup order**, not Kubernetes object mutation order. Helm still applies Deployment specs and ConfigMaps together. New containers read their ConfigMap-backed environment only after their init gates finish, but mutable mounted files, externally changed Secrets, and old ReplicaSet replacements are not an atomic configuration transaction. Use separately staged migration deployment and versioned configuration/Secrets if no object or old workload may see changed configuration before migration completion. After changing an external Secret, perform a Helm upgrade to roll pods; the chart does not watch or snapshot secret values. Pin image digests when immutable image identity is required.

Pool-specific API image/config overrides must remain schema-compatible with the shared migrator image and database. SQL changes must be backward-compatible with existing running workloads; rollback never reverses schema changes.

Existing rollout strategies still govern availability and spare capacity. `Recreate` and nonzero `maxUnavailable` can remove old capacity while replacements wait. With RollingUpdate and `maxUnavailable: 0`, old API replicas remain while the new API waits; exact ordering does not solve insufficient surge nodes. Local SSD cache warm-up is governed by readiness and `minReadySeconds`, not inferred from pod creation alone.

## Retention and rollback

Neither release Job has a TTL. Completed Jobs, their configuration, helper code, and per-revision RBAC use `helm.sh/resource-policy: keep`, so replacement pods and rollback revisions retain their prerequisites. Those phase records survive uninstall. The rollout observer uses a stable, namespace-only ServiceAccount that is not retained: disabling the gate removes it, allowing Helm 3 to recreate it cleanly when rolling back to an enabled revision. Old API replacements read the retained Job using their own API identity, not the observer account.

Delete retained resources only after no Pod/ReplicaSet references the revision and it is no longer needed for rollback. Do not delete the active revision's completed Jobs: replacements and autoscaling need those success records. Before reinstalling the same release name, deliberately clean up retained resources after confirming no old workloads reference them. Retry failed attempts with a new `helm upgrade` revision after fixing the cause.

Native Helm rollback runs the target revision's `pre-rollback` cleanup hook. Its separate identity can get/delete only that target's two Jobs and corresponding ConfigMaps, Roles, and RoleBindings. Cleanup issues deletion for both Jobs, waits for foreground deletion of both, then removes retained config/RBAC. Helm then recreates the target resources and reruns both phases. Migration-only releases clean up just their migration phase. This avoids the Helm 3 retained-resource restoration error (https://github.com/helm/helm/issues/13142) without overlapping the target's old and new Jobs.

Do not use `--no-hooks` on rollback. Automatic rollback follows the same hook path. Existing running application containers continue according to their rollout strategy; replacement pods can wait during cleanup. A failed cleanup blocks rollback and leaves its hook logs for diagnosis.

## Timeouts and verification

`migrations.timeoutSeconds` bounds the migration phase. `api.brainstoreStartupGate.timeoutSeconds` bounds exact rollout observation and the existing compatibility check. The rollout Job and its API wait include the migration plus rollout budgets; API progress deadlines also include the subsequent compatibility budget and startup margin.

Allow enough Helm timeout for migration, node allocation/cache readiness, and API startup. Use `--wait --wait-for-jobs` to observe migration/rollout failure; without waits Helm can return before these phases finish. Inspect the revision-specific rollout Job's logs to identify the role and incomplete condition. Pending replacement writer capacity must keep the new API blocked even when an old compatible writer remains Ready.
