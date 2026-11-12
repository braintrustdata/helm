# Release ordering

**Migrations complete → Brainstore finishes rolling out → all other applications start.**

With `deploymentOrdering.enabled: true` (default), one regular migration Job runs the shared API image's migration entry point for each Helm revision. Brainstore waits for it; all API pools, optional AI Gateway, and optional Loop Runtime wait for migrations and then check Brainstore Deployments and Pods directly. There is no separate rollout Job or rollout-configuration snapshot.

## What applications check

For every rendered reader, writer, fastreader, and automationwriter:

- The Deployment pod-template release marker is the waiting application's Helm revision or newer.
- `status.observedGeneration >= metadata.generation`.
- Updated, Ready, Available, and total replica counts equal the current desired count.
- All service-selected pods belong to that current rollout and are Ready; no old or terminating live pods remain.
- Desired and running images in nonzero Brainstore pools satisfy the waiting application's version floor.

The release marker prevents the previous fully Ready Deployment from passing before Helm updates it. Deployment generation counters are checked within each Deployment, not compared between API and Brainstore. Pod-template changes cover images and inline environment; the existing ConfigMap checksum covers chart-managed environment. Zero-replica roles must finish draining. Two successful fleet observations share one bounded deadline.

**Tradeoff:** older replacement API, Gateway, and Loop pods also wait during a newer unfinished Brainstore rollout. They accept a newer completed compatible release rather than waiting for their old configuration to become current again. Insufficient surge capacity can therefore block restarts as well as the new release.

## Input matrix

| Input | Behavior |
| --- | --- |
| `deploymentOrdering.enabled: true`; missing/null map or partial reused values | Migration Job, then direct completed-rollout and compatibility checks |
| `deploymentOrdering.enabled: false` | No chart-managed Job, waits, RBAC, helper configuration, hooks, or release markers; no intermediate mode |
| Same image with changed env/config; stale controller observation; old Ready writer/new Pending writer | New API, Gateway, and Loop containers remain blocked |
| Older application, newer Brainstore release | Wait during rollout; pass after the newer compatible rollout completes |
| Replica count missing/null/zero; optional automationwriter absent/partial | Preserve Kubernetes/default pool behavior; zero roles drain, absent pools stay omitted |
| Custom role names, namespaces, API pools | Checks and read-only permissions follow the rendered workload names and actual namespace |
| Stable API release tags with/without v or digest; SHA/numeric/custom/prerelease tags | Stable tags derive the floor; opaque API tags require explicit stable `deploymentOrdering.minimumVersion` |
| AWS/Google/Azure, Azure CSI, CA mounts, custom scheduling/security | Migrator inherits the shared API contract and independently mounts its required secrets; startup checks only read Kubernetes state |
| Missing/null timing or invalid enabled timing/image | Existing defaults apply; invalid enabled values fail rendering, unused disabled settings are ignored |
| Failed/missing migration Job, API errors, old/stray/terminating pods, readiness dwell | Fail closed within the configured budgets; no missing resource implies success |
| Upgrade/retry/rollback and replacement pods | Distinct migration Jobs; keep completed migration records; rollback resets only the target migration phase |

## Retention and rollback

The migration Job, its snapshot configuration, helper code, and exact-Job read RBAC are retained without a TTL for replacement pods and rollback. They survive uninstall. Do not delete records still referenced by active pods, ReplicaSets, or a needed rollback revision. Retry a failed migration with a new Helm upgrade after fixing its cause.

The target revision's `pre-rollback` hook deletes that migration Job and waits for foreground deletion before removing its config/RBAC. Helm then recreates the target phase. This avoids Helm 3 retained-resource restoration errors. Do not use `--no-hooks`; failed cleanup blocks rollback. SQL changes are never reversed and must remain compatible with existing workloads.

### Reinstalling after uninstall

Before reinstalling the same release name in the same namespace, stop all old workloads and delete its retained migration records. Helm restarts revision numbering at 1: reusing old records can skip migrations against a new database, reuse a failed Job, or fail on an immutable Job template.

```sh
release=<release-name>
namespace=<namespace>
selector="braintrust.dev/migration-release=$release"
kubectl -n "$namespace" delete job -l "$selector" --cascade=foreground --wait=true
kubectl -n "$namespace" delete configmap,role,rolebinding -l "$selector" --wait=true
```

Run this only after uninstall, never against records needed by an active release. Wait for both commands to succeed before reinstalling; deleting the Job first prevents its migrator from outliving its configuration.

## Limits and operation

These gates order **application-container startup**, not Kubernetes object updates. Helm still submits Deployment specs and ConfigMaps together. Mutable mounted configuration, externally updated Secrets, and old ReplicaSet replacements are not an atomic configuration transaction. Use separately staged migrations and versioned configuration if no object or old workload may observe changes before migrations finish. Perform a Helm upgrade after an external Secret change to roll pods; secret values are not snapshotted.

Use real Helm revisions. Pool-specific API overrides must remain schema-compatible with the shared migrator image/database. Existing rollout strategies determine availability; strict startup ordering does not provide spare nodes or guarantee cache warm-up beyond configured readiness and `minReadySeconds`.

`deploymentOrdering.timeoutSeconds` (default 1200) bounds each migration and rollout-wait phase separately; `pollIntervalSeconds` (default 5) must be smaller. `minimumVersion` is an optional shared stable floor; when empty each API pool derives its own image floor, and Gateway/Loop use the shared API image. `image`, `resources`, and `backoffLimit` configure the helpers and migration retry budget. API progress deadlines include two phase budgets plus 300 seconds; Gateway/Loop include two plus 600; Brainstore adds one phase budget to its configured rollout deadline.

Use a sufficient Helm timeout and `--wait --wait-for-jobs`; otherwise Helm can return before migrations or rollout complete. Inspect `wait-for-migrations` and `wait-for-brainstore` init logs for the condition holding startup.
