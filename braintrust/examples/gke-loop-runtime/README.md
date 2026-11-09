# Loop Runtime on GKE

Loop Runtime runs in the primary GKE cluster. Its sandboxes run on Sandbox
Service workers in a separate isolated cluster. Both clusters come from the
GCP Terraform module with:

```hcl
gke_isolated_workers = {
  network_cidr     = "10.64.0.0/16" # must not overlap existing ranges
  authorized_cidrs = ["192.0.2.10/32"] # replace with operator and CI egress ranges
  discovery        = { dns_name = "sandbox.<deployment>.internal" }
}
```

`<...>` values below come from `terraform output gke_isolated_workers` and
`terraform output loop_runtime_service_account`.

Use compatible API, Brainstore, and AI Gateway images for the runtime build.
Use the chart's Braintrust services version for Loop Runtime and Sandbox Service.
With `helm upgrade --reuse-values`, explicitly set `--set loopRuntime.image.tag=<services-version>`
to replace a retained runtime image tag, and update `image` in `sandbox-service.yaml` to the same version.
The API must report `ff_version` 82 or newer, and the Rust gateway must support
`x-bt-provider-model-map`.
Deploy the gateway with `aiGateway.enabled=true` first and wait for it to become
ready before applying this example, which turns on `aiGateway.useGateway`.
The TypeScript API proxy alone cannot restore the provider model IDs sent by Codex.

For EU or custom control planes, set `aiGateway.braintrustAppUrl`; Loop Runtime
uses the same URL. To override it for Loop only, set `BRAINTRUST_APP_URL` in
`loopRuntime.extraEnvVars`.

1. Create the mTLS Secrets in both clusters:

   ```sh
   braintrust/examples/gke-loop-runtime/create-sandbox-tls.sh "<discovery.hostname>" <worker> <primary>
   ```

2. Install the workers. The configured Sandbox Service image prepares local SSDs before starting the worker:

   ```sh
   helm --kube-context <worker> upgrade --install sandbox-service \
     https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/1f4b6826c61064575b3db956b67625133257bd8a6e49e4ea86cdb55012dd53a7/sandbox-service-0.1.0.tgz \
     --namespace braintrust-sandbox --values braintrust/examples/gke-loop-runtime/sandbox-service.yaml --wait
   ```

3. Install this chart with your GKE values plus `values.yaml` from this directory.
   For a Standard primary cluster, set `loopRuntime.nodeSelector` to
   `{"braintrust/workload": "services"}` to use the services pool:

   ```sh
   helm --kube-context <primary> upgrade --install braintrust ./braintrust --namespace braintrust \
     --values <your GKE values> --values braintrust/examples/gke-loop-runtime/values.yaml --wait
   ```

Workers run at a fixed count. One `c4-standard-8-lssd` worker holds about seven
1 vCPU / 2 GiB sandboxes; beyond that, new sandboxes fail with
`CapacityExhausted`. Raise `replicas` in `sandbox-service.yaml` to add workers.

## Filesystem retention

The worker package and image pinned above use node-local storage. They do not
enable durable filesystem checkpoints. To enable retention, use a published
Sandbox Service chart and image whose release supports filesystem retention,
alongside a compatible Loop Runtime image. Updating only the image or passing
retention values to the older chart does not enable this feature.

Apply the GCP Terraform module's checkpoint-storage update first. Its
`gke_isolated_workers.checkpoint_store` output identifies a dedicated private GCS
bucket. Copy that URI into `sandbox-service-retention.yaml`. Worker nodes receive
object access to that bucket through their node identity; the primary runtime and
isolated services pool do not need checkpoint access.

Set `SANDBOX_SERVICE_CHART_URL` and `SANDBOX_SERVICE_IMAGE` to the compatible
published artifacts, then apply the overlay to the worker release:

```sh
: "${SANDBOX_SERVICE_CHART_URL:?set a retention-capable published worker chart URL}"
: "${SANDBOX_SERVICE_IMAGE:?set a compatible published worker image}"
helm --kube-context <worker> upgrade --install sandbox-service \
  "$SANDBOX_SERVICE_CHART_URL" --namespace braintrust-sandbox \
  --values braintrust/examples/gke-loop-runtime/sandbox-service.yaml \
  --values braintrust/examples/gke-loop-runtime/sandbox-service-retention.yaml \
  --set-string image="$SANDBOX_SERVICE_IMAGE" --wait
```

Existing releases using `--reuse-values` must explicitly set both retention
values and the compatible image; do not rely on new chart defaults to replace
retained settings. The worker controller continues to own replica changes and
draining. Keep its eviction protections and provision capacity for a replacement
worker during upgrades.

The workers still use local SSDs formatted as XFS for their working disks.
Checkpoints preserve files at completed idle sleep or preserving drain; they do
not preserve running processes or edits since the last completed checkpoint.
Before relying on recovery, verify that a file survives sleep, wake, and worker
node replacement in the target deployment. Allow disk headroom for retained
local checkpoints as well as active sandboxes.

Keep the checkpoint bucket and URI stable while retained sandboxes exist.
Coordinate any return to workers without retention support before disabling it.
Removing the worker infrastructure retains the bucket; save its URI and retain
the encryption key, and import the original storage before re-enabling workers
with existing checkpoints. See the Terraform module's
[storage lifecycle](https://github.com/braintrustdata/terraform-google-braintrust-data-plane/blob/main/ISOLATED_WORKERS.md#storage-and-lifecycle).

Loop Runtime defaults to one pod. Rolling updates use `maxSurge: 1` and
`maxUnavailable: 0`, giving a 1 → 2 → 1 rollout. Increase `loopRuntime.replicas`
for more capacity or pod redundancy.
With `helm upgrade --reuse-values`, add `--set loopRuntime.replicas=1`
to replace a retained count of two.
Each runtime pod claims at most two conversations and has a 256Mi in-memory cache budget.

For conversation-based runtime autoscaling, set `loopRuntime.autoscaling.enabled: true`.
This uses the same GKE custom-metrics prerequisites as `api.autoscaling` and defaults
to 1–50 pods at 50% claimed conversation capacity, with a five-minute downscale window.
The application must expose the gauge `loop_runtime_claimed_conversation_utilization`
(0–100 percent) at `/metrics` on the runtime HTTP port. `metricsPath`, `minReplicas`,
`maxReplicas`, and `targetConversationUtilization` are configurable under `loopRuntime.autoscaling`.
An image without that endpoint or gauge cannot drive metric-based scaling; there is no image-version gate.
The HPA owns replicas while enabled; disabling it restores `loopRuntime.replicas`.
Enabling it on an existing Deployment can initially reset the count to one.
This scales runtime pods only; provision worker replicas separately for the expected sandbox capacity.

Sandbox administration is disabled by default. Set `loopRuntime.enableSandboxAdmin: true`
to allow authorized sysadmins to inspect and terminate sandboxes.

To shut down, set the worker release to `replicas: 0`, wait for the workers to
drain, then disable `loopRuntime.enabled` and uninstall the worker release. Its
pre-delete hook refuses to uninstall until workers drain.
