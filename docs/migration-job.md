# Release ordering

**Migrations complete → readers/writers finish rolling out → API starts.**

One regular migration Job runs the shared API image's migration entry point for each Helm revision. Every new application container waits for it. The existing API startup init container then checks Brainstore Deployments and Pods directly. There is no rollout Job, observer ServiceAccount, or rollout-configuration snapshot.

## What the API checks

For every rendered reader, writer, fastreader, and automationwriter:

- The Deployment pod-template release marker is this API's Helm revision or newer.
- `status.observedGeneration >= metadata.generation`.
- Updated, Ready, Available, and total replica counts equal the current desired count.
- All service-selected pods belong to that current rollout and are Ready; no old or terminating live pods remain.
- Desired and running Brainstore images satisfy this API pool's version floor.

The release marker prevents the previous fully Ready Deployment from passing before Helm updates it. Deployment generation counters are checked within each Deployment, not compared between API and Brainstore. Pod-template changes cover images and inline environment; the existing ConfigMap checksum covers chart-managed environment. Zero-replica roles must finish draining. Two successful fleet observations share one bounded deadline.

**Tradeoff:** an older API replacement also waits during a newer unfinished Brainstore rollout. It accepts a newer completed compatible release rather than waiting for its old configuration to become current again. Insufficient surge capacity can therefore block replacement APIs as well as the new release.

## Input matrix

| Input | Behavior |
| --- | --- |
| Both gates enabled; missing/null maps or partial reused values | Migration Job, then direct completed-rollout and compatibility checks |
| `migrations.enabled: false` | No migration Job; previous compatible-capacity gate and API-startup migration behavior |
| `api.brainstoreStartupGate.enabled: false` | Migration-only startup ordering; both disabled means no chart-managed gates |
| Same image with changed env/config; stale controller observation; old Ready writer/new Pending writer | New API remains blocked |
| Older API, newer Brainstore release | Wait during rollout; pass after the newer compatible rollout completes |
| Replica count missing/null/zero; optional automationwriter absent/partial | Preserve Kubernetes/default pool behavior; zero roles drain, absent pools stay omitted |
| Custom role names, namespaces, API pools | Checks and read-only permissions follow the rendered workload names and actual namespace |
| Release tags with/without v, SHA/numeric/custom/prerelease/digest API tags | Existing image handling is unchanged; opaque API tags require an explicit stable minimumVersion |
| AWS/Google/Azure, Azure CSI, CA mounts, custom scheduling/security | Migrator inherits the shared API contract and independently mounts its required secrets; startup checks only read Kubernetes state |
| Missing/null timing or invalid enabled timing/image | Existing defaults apply; invalid enabled values fail rendering, unused disabled settings are ignored |
| Failed/missing migration Job, API errors, old/stray/terminating pods, readiness dwell | Fail closed within the configured budgets; no missing resource implies success |
| Upgrade/retry/rollback and replacement pods | Distinct migration Jobs; keep completed migration records; rollback resets only the target migration phase |

## Retention and rollback

The migration Job, its snapshot configuration, helper code, and exact-Job read RBAC are retained without a TTL for replacement pods and rollback. They survive uninstall. Do not delete records still referenced by active pods, ReplicaSets, or a needed rollback revision. Retry a failed migration with a new Helm upgrade after fixing its cause.

The target revision's `pre-rollback` hook deletes that migration Job and waits for foreground deletion before removing its config/RBAC. Helm then recreates the target phase. This avoids Helm 3 retained-resource restoration errors. Do not use `--no-hooks`; failed cleanup blocks rollback. SQL changes are never reversed and must remain compatible with existing workloads.

## Limits and operation

These gates order **application-container startup**, not Kubernetes object updates. Helm still submits Deployment specs and ConfigMaps together. Mutable mounted configuration, externally updated Secrets, and old ReplicaSet replacements are not an atomic configuration transaction. Use separately staged migrations and versioned configuration if no object or old workload may observe changes before migrations finish. Perform a Helm upgrade after an external Secret change to roll pods; secret values are not snapshotted.

Use real Helm revisions, not repeated `helm template | kubectl apply`. Pool-specific API overrides must remain schema-compatible with the shared migrator image/database. Existing rollout strategies determine availability; strict startup ordering does not provide spare nodes or guarantee cache warm-up beyond configured readiness and `minReadySeconds`.

`migrations.timeoutSeconds` bounds migration waiting. `api.brainstoreStartupGate.timeoutSeconds` bounds direct rollout observation. API progress deadlines include both budgets plus startup margin. Use sufficient Helm timeout and `--wait --wait-for-jobs`; otherwise Helm can return before migrations or rollout complete. Inspect API `wait-for-brainstore` init logs for the role and condition holding startup.
