# Brainstore startup gate: draft design and canary plan

## Problem and intended behavior

The API binding change requires a newer Brainstore query protocol. The chart
currently rolls API and Brainstore Deployments independently, so new API processes
can issue unsupported queries to older Brainstore pods during a single upgrade.

An API init container makes read-only Kubernetes API requests before the API
process starts. It checks reader, writer, and fastreader Deployments and every
Pod selected by their Services. The API process remains stopped until:

- Kubernetes has observed each desired Deployment generation.
- The desired Brainstore images and all potentially active backend containers
  are compatible with the API pod's minimum Brainstore release.
- Each active role has at least its desired replica count Ready and Available.
- Incompatible older pods have exited, including pods still terminating.
- Two observations, 15 seconds apart by default, show compatible capacity at
  the same desired Deployment revisions. Compatible Pod replacement alone does
  not reset confirmation and strand older APIs during a later rollout.

A role scaled to zero waits until its remaining pods have disappeared. Failed
and Succeeded pods no longer contain active query backends and do not block.
The checker reads the actual running container image as well as the Pod spec.

Compatible later Brainstore rollouts need not finish before an older API pod
restarts. Waiting for exact old images or for every future rollout to finish
would reduce available API capacity unnecessarily. The old API pod retains its
own minimum version in its environment even when the next chart changes the
shared helper ConfigMap. Keep the helper's environment interface compatible
with previous API pods when changing the script.

## Compatibility assumptions

By default an API pool requires its own stable release version of Brainstore,
or a later stable release in the same major version. This is deliberately
conservative for patch versions. The explicit `minimumVersion` override is a
release-owner declaration shared across all pools, not automatic negotiation.
Do not lower it merely to get a blocked deployment through.

This checks the declared image versions, not query protocol capabilities or
binary provenance. It assumes stable release tags accurately identify immutable
release artifacts and that newer Brainstore releases in the supported major
preserve older API behavior. In particular, the 2.15 canary must include the
existing opt-in column-order fix that preserves old API response parsing.

No API or Brainstore code changes are required to implement the barrier under
that contract. Opaque Brainstore SHA tags and digest-only images cannot establish
a compatible release. Supporting them automatically would require additional
trusted release metadata or application capability reporting; this draft does
not invent that evidence. An opaque API image can declare its required stable
Brainstore floor explicitly.

This does not replace the pre-2.0 migration steps or WAL/storage-format activation
requirements. A cross-major transition requires its own compatibility design.

## Failure and operational behavior

- Missing Deployments, unpropagated RBAC, Kubernetes API failures, insufficient
  capacity, and unknown image versions never allow the API process to start.
- Each init attempt has a bounded wait budget, default 1,200 seconds. Failure
  exits nonzero; Kubernetes retries the init container. Recovery can complete
  when the backend state or permission issue is corrected. There is no fail-open
  timeout. The API Deployment progress deadline adds five minutes for API
  initialization after that wait budget. Use a Helm timeout long enough for the
  combined rollout during the canary (for example, `--timeout 25m`).
- API pools require `maxUnavailable: 0` and room for surge pods so existing APIs
  continue serving while new pods wait. Those waiting pods reserve their full
  future API resources under Kubernetes scheduling rules. Verify combined API
  and Brainstore surge capacity/quota; lack of room can stall the upgrade.
- Read permissions are namespace-scoped Pod lists and `get` on only the three
  named Brainstore Deployments. A projected, rotating token and the cluster CA
  are mounted into the init container. TLS verification remains enabled. The
  helper does not read application Secrets or mutate any Kubernetes resource.
- The pinned helper image supports amd64 and arm64. Mirrored images must retain
  Python 3 and the standard library. No packages are installed at pod startup.
- API-pod network policies must allow HTTPS to the Kubernetes API endpoint.
  The checker polls once per initializing API pod; observe API-server load at
  production pool sizes before considering watch-based optimization.

## Rollback and completion boundaries

This gate controls API process startup, not subsequent backend changes. If
Brainstore rolls back while newer API processes still run, queries can fail
until those APIs are replaced. This draft accepts that exceptional window and
does not implement reverse-order orchestration. Validate that an actual rollback
converges rather than leaving pods stuck in init.

Within one major version an older API floor accepts a newer Brainstore version,
so an API-first rollback can start without waiting for Brainstore to downgrade.
A simultaneous rollback may wait for compatible backend capacity and can expose
the query-failure window above. No availability claim is made for that window.

Helm `--wait` uses minimum ready counts, not full replacement of every old pod.
This feature therefore provides a startup barrier but does not add synchronous
full-rollout semantics to the Helm CLI. Verify Kubernetes rollout status when
testing; automation requiring full completion must already monitor that status
or add a separate read-only completion check.

## Validation completed locally

The helper's standard-library tests cover mixed versions, terminating old pods,
running-image/spec disagreement, later compatible rollouts, capacity/readiness,
zero-replica roles, rotating credentials, pagination, consecutive observations,
transient Kubernetes failures, and fail-closed timeouts. Helm tests cover all API
pools, namespace/RBAC wiring, version overrides, labels, reused values, disabled
defaults, and rollout-policy validation.

These tests are not a Kubernetes deployment or application query canary.

## Proposed canary (separate deployment authorization)

Use an isolated non-production deployment with approved image releases and real
Brainstore dependencies. Keep the gate opt-in and do not bump chart/image defaults
until release coordination and this validation are complete. Keep workload
topology and routing stable for the first image-upgrade canary; the chart's
existing staged activation of workload isolation is a separate dependency.

1. Enable the gate on a known compatible baseline. Confirm helper image pull,
   non-root execution, token/CA projection, RBAC, and API-server network access.
   Confirm existing API/Brainstore health and representative queries.
2. Upgrade API and Brainstore together in one Helm operation. Record API init
   logs, running container versions, Brainstore pod termination, and rollout
   status for each enabled role/pool. New APIs must remain in init while any
   incompatible old Brainstore pod is active. Run representative queries through
   the entire transition, including the column-order response path.
3. Extend old Brainstore termination to exercise the existing-connection window.
   The new API process must not start just because old pods are unready.
4. Exercise an API-only image bump while Brainstore stays old. New APIs must
   remain blocked and the old API capacity must remain available.
5. Start a later compatible Brainstore rollout while retaining an older gated
   API release. Recreate an older API pod during the mixed Brainstore state.
   It must start when compatible capacity is available, without demanding its
   original exact Brainstore image.
6. Make a target Brainstore image unavailable or unready. Confirm that blocked
   API pods never start after a helper timeout, and that fixing the backend lets
   them recover. Check quota/scheduling events and Kubernetes API request load.
7. Test a rollback with both partially updated and fully updated APIs. Temporary
   query errors are acceptable for this experiment; sustained inability to
   converge is not. Include rollback to a chart predating the gate.
8. Test a fresh installation. Brainstore must become Ready without requiring
   the still-gated API. The current Brainstore `/` readiness endpoint is a local
   ping, but initialization dependencies still need runtime validation.

If version tags do not accurately identify protocol support, later Brainstore
breaks an older supported API, or startup depends circularly on a newer API,
bring that concrete finding back to API/Brainstore engineering. Infrastructure
cannot manufacture compatibility or resolve a genuine application dependency
cycle.
