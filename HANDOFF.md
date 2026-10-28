# Handoff: EKS / AKS API autoscaling metrics path

**Audience:** Yash (and anyone finishing [#94](https://github.com/braintrustdata/helm/pull/94) / [#96](https://github.com/braintrustdata/helm/pull/96))  
**Status:** Rebased onto `main` after GKE autoscaling merged ([#95](https://github.com/braintrustdata/helm/pull/95)). Chart unit tests pass. EKS sandbox was soaked earlier; re-soak before ship. AKS not soaked.  
**Date:** 2026-10-28

This note explains **why we chose in-chart Prometheus + prometheus-adapter for EKS/AKS**, how that relates to GKE’s `AutoscalingMetric`, and whether that choice still holds after re-checking the ecosystem.

---

## What we are trying to do

Match **ECS api-ts autoscaling signals** on Kubernetes:

| Signal | Target (prom-canonical) |
| --- | --- |
| CPU | 50% utilization on the `api` container (`ContainerResource`) |
| Event-loop utilization | average `0.4` (ratio 0–1) |
| Event-loop delay mean | average `0.05` seconds |

Product constraints locked earlier:

1. **Braintrust owns** the autoscaling metrics path (not “customer already has Prometheus”).
2. **Same app contract everywhere:** API health `/metrics` (`ENABLE_PROMETHEUS_METRICS`, data plane **v2.9.0+**).
3. **HPA scales pods**; node scaling (Karpenter / NAP) is separate.
4. **api-ts only** for v1 (brainstore HPA out of scope).
5. **Isolated from** Datadog / Pulse / `controlPlaneTelemetry`.

GKE (#95) uses Google’s managed **`AutoscalingMetric`** CRD: the control plane scrapes each pool’s `/metrics` and feeds HPA. EKS and AKS have **no equivalent managed CRD**.

---

## Architecture we shipped on EKS / AKS

```
api pods (:8001/metrics)
        │ scrape
        ▼
in-chart Prometheus (short retention)
        │ query
        ▼
prometheus-adapter  ──APIService──▶  custom.metrics.k8s.io
        │
        ▼
HorizontalPodAutoscaler (Pods metrics: event_loop_*)
```

- Templates: `api-autoscaling-prometheus.yaml`, `api-autoscaling-prometheus-adapter.yaml`, cloud-branched `api-hpa.yaml`.
- GKE keeps `AutoscalingMetric`; EKS/AKS skip that CRD and skip the GKE API discovery fail in `braintrust.apiAutoscaling.validate`.
- With workload isolation, Prometheus scrapes **all** pool apps; each pool still gets its own HPA (or fixed replicas if that pool disables autoscaling).

PRs:

| PR | Branch | Base | Role |
| --- | --- | --- | --- |
| [#94](https://github.com/braintrustdata/helm/pull/94) | `erikdw/api-autoscaling-aws-prom` | `main` | EKS (`cloud=aws`) |
| [#96](https://github.com/braintrustdata/helm/pull/96) | `erikdw/api-autoscaling-aks` | #94 | AKS (`cloud=azure`) |

Merge order: **#94 then #96**.

---

## Original rationale for Prometheus + prometheus-adapter

### Why not “just HPA”?

HPA only natively understands **resource** metrics (`metrics.k8s.io` via metrics-server). Event-loop gauges are **application metrics**. Something must implement `custom.metrics.k8s.io` (or `external.metrics.k8s.io`) and register an APIService, or HPA cannot see them.

### Why Prometheus in the chart?

We need a scrape + store of the Prometheus text format already emitted by api-ts. Options were:

| Option | Why we rejected or deferred |
| --- | --- |
| Customer BYO Prometheus | Violates “Braintrust owns autoscaling metrics.” |
| CloudWatch push (ECS path) | AWS-only; different contract from GKE `/metrics`; needs a CloudWatch→HPA adapter; duplicates ECS instrumentation for K8s. |
| Amazon Managed Prometheus / Azure Monitor | Still need a bridge to HPA; cloud-specific IAM and ops; harder to keep one path for EKS **and** AKS. |
| Sidecar scraper per API pod | More moving parts on the hot path; still need a metrics API for HPA. |

A **small in-chart Prometheus** (1h retention, scrape only API pool pods) is the simplest Braintrust-owned store that matches the app’s existing exposition format.

### Why prometheus-adapter (vs inventing a GKE-like controller)?

GKE `AutoscalingMetric` is roughly: **declare which pod gauges to scrape → managed controller publishes them for HPA**. On EKS there is no first-party twin.

**Building our own “AutoscalingMetric for EKS”** would mean owning:

1. A CRD and validation UX.
2. A scraper (or pull agent) hitting every API pod `/metrics`.
3. An aggregated API server implementing `custom.metrics.k8s.io` (auth delegation, discovery, watch semantics HPA expects).
4. Multi-cloud packaging, upgrades, RBAC, failure modes, and support for self-hosted customers.

That is a **metrics-adapter product**, not a thin Helm feature. Cost and risk dwarf the benefit when mature adapters already do (3)+(part of 2) and Prometheus already does scrape/store.

**prometheus-adapter** was the standard kubernetes-sigs bridge: PromQL series → `custom.metrics.k8s.io` → native `autoscaling/v2` HPA with `type: Pods`. It kept:

- The same **HPA object shape** as GKE (CPU + two Pods metrics).
- The same **metric semantics** (per-pod averages of ELU / delay).
- A stack we can also turn on for **AKS** with a one-line cloud gate (#96).

Closest “GKE-like open source” alternatives (e.g. Zalando/Demonware `kube-metrics-adapter`) still leave us maintaining a less-common adapter and do not remove the need for scrape + APIService ownership.

---

## Reanalysis (2026-10): is prometheus-adapter still the right move?

**Short answer:** It remains a **reasonable path to finish and soak #94/#96**, and it is still better than writing a GKE-style controller. It is **no longer the clear long-term default** — plan a near-term evaluation of **KEDA** before treating Prom+adapter as permanent.

### What changed

- SIG Instrumentation has an **open proposal to deprecate prometheus-adapter** and steer users to **KEDA** ([kubernetes-sigs/prometheus-adapter#701](https://github.com/kubernetes-sigs/prometheus-adapter/issues/701), triage/accepted; deprecation not formally announced as of late Oct 2026, chart still publishing).
- AWS publishes guidance for **KEDA on EKS** for custom/event-driven autoscaling.
- Community docs increasingly warn: do not run KEDA and prometheus-adapter both as owners of `external.metrics.k8s.io`.

### Comparison for *our* use case

We need: average of two **pod gauges** + CPU `ContainerResource`, minReplicas ≥ 1 (no scale-to-zero), Braintrust-owned scrape, portable EKS+AKS.

| Approach | Fit | Pros | Cons |
| --- | --- | --- | --- |
| **Prom + prometheus-adapter (current PRs)** | Good short-term | Native HPA; `custom.metrics` Pods metrics; already implemented + unit-tested; prior EKS soak; AKS reuse | Upstream deprecation trajectory; APIService conflict if cluster already has an adapter; we run a second Prom |
| **Prom + KEDA (Prometheus trigger / ScaledObject)** | Strong medium-term | Actively maintained; AWS-aligned; one operator story; avoids PA deprecation | Different UX (`ScaledObject` vs hand-written HPA); typically `external.metrics`; need to prove ELU/delay average semantics match ECS/GKE; still need a Prom (or AMP) |
| **Homegrown AutoscalingMetric-like controller** | Poor | Closest DX to GKE CRD | High build/ops cost; reinvent metrics API; no customer demand that outweighs adapters |
| **CloudWatch metrics adapter** | Poor for unified K8s | Reuses ECS push mental model | AWS-only; diverges from GKE `/metrics`; extra IAM |
| **BYO customer Prom/adapter** | Poor vs product goal | Less chart bulk | Breaks “turn on autoscaling” for self-hosted |

### Recommendation for Yash

1. **Ship / finish #94 on Prom+adapter** if the goal is parity and soak soon — do not block on rewriting to KEDA mid-flight unless review insists.
2. **Explicit follow-up ticket:** spike KEDA replacing the adapter (keep in-chart or AMP Prometheus; keep `/metrics` contract; match replica behavior and metric targets). Prefer one path for EKS+AKS.
3. **Do not** invest in a Braintrust-owned GKE `AutoscalingMetric` clone unless Google’s managed path becomes unavailable on GKE *and* no adapter/KEDA path meets support needs.
4. Before production: check the target cluster for an existing `v1beta1.custom.metrics.k8s.io` / `external.metrics.k8s.io` APIService — dual adapters break HPA.

---

## Remaining work checklist

- [ ] Re-soak EKS (`api.autoscaling.enabled=true`): Prom targets up on `:8001`, HPA has non-empty `event_loop_*`, scale under load.
- [ ] First AKS soak (#96) with same checks.
- [ ] Example values file for EKS (and AKS) analogous to `examples/google-api-isolation-autoscaling/`.
- [ ] Document APIService conflict / single-adapter assumption in README.
- [ ] Decide PA → KEDA migration timing (before or after first customer GA).
- [ ] Confirm image pins (`prom/prometheus`, `prometheus-adapter`) and resource requests for small sandboxes vs prod.

### Local commands

```bash
cd /path/to/helm
./test.sh
```

### Unrelated local stash

Before this rebase, unrelated WIP was stashed on the old AKS checkout:

`stash@{0}: unrelated-wip-before-autoscaling-rebase` (configmap / helpers bits). Not part of these PRs.

---

## Decision summary

| Question | Answer |
| --- | --- |
| Why Prom+adapter originally? | Braintrust-owned scrape + standard bridge to HPA custom metrics; same `/metrics` as GKE; portable to AKS; far cheaper than a custom GKE-like controller. |
| Still right for EKS *today*? | **Yes to complete and validate the open PRs.** Treat as **interim** given PA’s deprecation proposal; spike **KEDA** as the likely durable EKS/AKS backend. |
| Build our own AutoscalingMetric controller? | **No** — wrong ROI; duplicates adapter + aggregation-layer work Google already runs as a managed Preview on GKE. |
