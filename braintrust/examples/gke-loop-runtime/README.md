# Loop Runtime and Sandbox Service on GKE

Loop Runtime runs in the primary GKE cluster with this chart. Its sandboxes run
on Sandbox Service workers in a separate isolated GKE cluster, installed from
the published worker chart. The GCP Terraform module creates both clusters, the
worker node pool, private DNS, firewall rules, and the Loop Runtime identity
when `gke_isolated_workers` is set with `discovery`.

## Connections

| From | To | Port | Address |
| --- | --- | --- | --- |
| Loop Runtime, worker controller | Workers | TCP 9400, mTLS | `discovery.endpoint` |
| Sandboxes (Git) | Loop Runtime forward proxy | TCP 4002 | `loop_egress.url`, an internal load balancer on a reserved address |

## Install

All `<...>` values come from `terraform output gke_isolated_workers` and
`terraform output loop_runtime_service_account`.

1. Create the mutual TLS Secrets in both clusters. The script refuses to
   replace existing Secrets.

   ```sh
   braintrust/examples/gke-loop-runtime/create-sandbox-tls.sh \
     "<discovery.hostname>" <worker cluster context> <primary cluster context>
   ```

2. Install the workers in the isolated cluster, using the worker chart that
   matches the pinned images.

   ```sh
   helm --kube-context <worker cluster context> upgrade --install sandbox-service \
     https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/cd27a3983e304932a7e1617adf3105c2b7320a340f26bfea577df781827142f9/sandbox-service-0.1.0.tgz \
     --namespace braintrust-sandbox \
     --values braintrust/examples/gke-loop-runtime/sandbox-service.yaml --wait
   ```

3. Install or upgrade this chart in the primary cluster with your existing GKE
   values plus `values.yaml` from this directory.

   ```sh
   helm --kube-context <primary cluster context> upgrade --install braintrust ./braintrust \
     --namespace braintrust \
     --values <your GKE values> \
     --values braintrust/examples/gke-loop-runtime/values.yaml --wait
   ```

## Capacity

Workers run at a fixed count (`replicas: 1`). One 8-vCPU worker node holds about
seven 1 vCPU / 2 GiB sandboxes. When every slot is taken, new sandboxes fail
with `CapacityExhausted`. Raise `replicas` in `sandbox-service.yaml` to add
workers. The worker node pool autoscales up to its Terraform maximum and keeps
room for one extra worker during rollouts.

## Upgrade and shutdown

Upgrade both releases with the same values, and keep the pinned image tags in
`values.yaml` (chart defaults) and `sandbox-service.yaml` together. The worker
controller owns scale-down and replacement; do not force-delete workers or
their nodes. To shut down, stop new Loop work, upgrade the worker release with
`replicas: 0`, wait for
`kubectl -n braintrust-sandbox get configmap sandbox-rollout -o yaml` to show
zero workers, then disable `loopRuntime.enabled` and uninstall the worker
release. Its pre-delete hook refuses to uninstall until workers drain.
