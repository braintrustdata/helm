# Release migration Job

## Input matrix and intended behavior

| Input | Behavior |
| --- | --- |
| Feature enabled (default), missing/null map, partial reused values | One regular Job per Helm release revision; all chart Deployment pods wait for that revision's successful Job |
| Feature disabled; enabled to disabled | No new migration resources or waits; API startup continues to run migrations as before |
| Disabled to enabled | New revision Job and pod gates are created |
| API image release tags with/without v, full/short/numeric SHA, prerelease, custom tags, digest | Pass image through unchanged; existing Brainstore version gate still requires its explicit minimum for non-release tags |
| AWS, Google, Azure; provider examples | Job uses shared API identity, configuration, secrets, CA, scheduling and security settings |
| Azure Key Vault CSI enabled | Migration Job mounts CSI secrets itself before application pods start |
| API workload isolation on/off; custom pool identities | One shared API-image migrator; every pool waits, with read-only Job RBAC for each account |
| Brainstore roles enabled/zero/null replicas; automation writer missing/partial map | Preserve existing replica/default rules; every rendered pod template carries the wait |
| Optional gateway and Loop runtime enabled/disabled | Every rendered Deployment waits; disabled workloads stay omitted |
| Missing/null timing; empty/zero timing; empty image | Missing/null use defaults after Helm coalescing; empty/zero timing and empty image fail when enabled; disabled configuration does not validate unused settings |
| Upgrade/retry, old ReplicaSet replacement, rollback | Distinct revision names; keep completed Jobs and their config and read permissions; rollback cleans and recreates the target revision resources, rerunning migrations |
| Migration failure, missing Job, API errors | Fail closed; bounded waits and Job execution; application containers remain stopped |
| Global/API/role labels | No mutation or leakage into other workloads; Job must not match API Service selectors |

## Lifecycle

This feature requires Helm install/upgrade release revisions. Plain `helm template`
plus apply (including render-only GitOps controllers) does not increment revisions;
use a Helm release controller rather than repeatedly applying revision 1.

A release snapshots API ConfigMap data and runs the shared `api.image` migration
entry point. Pool-specific API image/config overrides must remain schema-compatible
with that shared image and database. The API startup migration check remains for
compatibility with other deployment methods. SQL changes must be compatible with
old running pods; rollback does not undo database changes. Existing rollout
strategies still govern availability (Recreate and nonzero maxUnavailable can
remove old capacity while replacements wait).

Jobs have no TTL. Jobs, migration configuration, gate code and revision-specific
RBAC use `helm.sh/resource-policy: keep`, so old ReplicaSets and rollback revisions
can still start pods. These resources also survive uninstall. Delete retained
resources only after no Pod/ReplicaSet references that revision and it is no longer
needed for rollback. Before reinstalling the same release name, clean up its
retained resources after ensuring no old workload references them. Do not delete
the active revision's Job: future replacements/autoscaling need its success record.
A failed Job is retried with a new `helm upgrade` revision after fixing its cause.

Native Helm rollback executes a `pre-rollback` cleanup hook with a separate service
account allowed only to get/delete the target revision's Job, two ConfigMaps, Role
and RoleBinding. It waits for foreground Job deletion before Helm recreates the
target resources and reruns the migration check. This avoids the Helm 3 retained-
resource rollback error (https://github.com/helm/helm/issues/13142). Existing running
application containers keep running, but replacements can wait during cleanup.
Do not use `--no-hooks` on rollback. Automatic rollback follows the same hook path.
Schema changes are never reversed. Helm rollback must have time for cleanup plus
migration plus application readiness. Failed cleanup blocks rollback; its hook Job
and logs remain for diagnosis.

Allow enough Helm timeout for migration plus Brainstore rollout and the existing
API compatibility gate. `--wait --wait-for-jobs` makes Helm report migration failure;
without waits, Helm can return before migration/rollout completion.
