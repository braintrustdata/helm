# Loop Runtime on GKE

Set `enable_loop_runtime = true` in the
[GCP data-plane module](https://github.com/braintrustdata/terraform-google-braintrust-data-plane#loop-runtime-infrastructure)
and apply infrastructure first. Loop Runtime stays in the primary cluster;
Sandbox Service workers run in a separate isolated cluster. The primary keeps
its existing Autopilot or Standard mode.

## Helm-managed primary release

Use the standalone
[Loop setup module](https://github.com/braintrustdata/terraform-google-braintrust-gke-app/tree/main/modules/loop-runtime-helm)
with the infra output and the same primary chart version:

```hcl
module "loop" {
  source = "github.com/braintrustdata/terraform-google-braintrust-gke-app//modules/loop-runtime-helm?ref=<module-version>"

  loop_runtime       = try(data.terraform_remote_state.infra.outputs.loop_runtime, null)
  helm_chart_version = var.helm_chart_version
}

output "loop_helm_values" {
  value     = module.loop.helm_values_yaml
  sensitive = true
}
```

Apply this deployment root. It configures worker providers, installs workers,
and creates mTLS automatically, without managing the primary application.
Pass its generated overlay after your existing Helm values:

```sh
umask 077
terraform output -raw loop_helm_values > loop-values.yaml
helm upgrade --install braintrust oci://public.ecr.aws/braintrust/helm/braintrust \
  --namespace braintrust --create-namespace --version <chart-version> \
  --reset-then-reuse-values --values <existing-values.yaml> \
  --values loop-values.yaml --wait
```

The overlay supplies discovery, workload identity, egress, matching image tags,
AI Gateway routing and client TLS. The chart creates `sandbox-client-tls`
and rolls the runtime when its certificate changes. Keep this file out of source
control: it contains private keys. Terraform and Helm release state also contain
TLS credentials. The selected primary chart must support
`loopRuntime.sandbox.clientTls`; the module rejects older releases.
See the [complete example](https://github.com/braintrustdata/terraform-google-braintrust-gke-app/tree/main/examples/helm-loop-runtime).

## Terraform-managed primary release

The [GKE app module](https://github.com/braintrustdata/terraform-google-braintrust-gke-app#managed-loop-runtime)
uses the same worker and TLS implementation and supplies the generated values
to its Helm release automatically. Pass the infra output once:

```hcl
loop_runtime = try(data.terraform_remote_state.infra.outputs.loop_runtime, null)
```

Apply infrastructure, then the app root. There is no separate app enable flag.
See its [remote-state example](https://github.com/braintrustdata/terraform-google-braintrust-gke-app/tree/main/examples/remote-state-app-root).
The [AWS Terraform module](https://github.com/braintrustdata/terraform-aws-braintrust-data-plane#loop-runtime)
also provides `enable_loop_runtime` for ECS; this Helm runtime supports Google Cloud.

## Manual Helm installation

The remaining steps are for installations that manage Helm releases directly.
Skip them when using the managed GKE app module, which owns these releases and
secrets. Provision standalone workers with the GCP infrastructure module:

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
     https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/062fbf5e1090e1f624eda6d6f9dfe7af05921e3d90a6405d330feb7866775ad2/sandbox-service-0.1.0.tgz \
     --namespace braintrust-sandbox --values braintrust/examples/gke-loop-runtime/sandbox-service.yaml --wait
   ```

3. Install this chart with your GKE values plus `values.yaml` from this directory.
   For a Standard primary cluster, set `loopRuntime.nodeSelector` to
   `{"braintrust/workload": "services"}` to use the services pool:

   ```sh
   helm --kube-context <primary> upgrade --install braintrust ./braintrust --namespace braintrust \
     --values <your GKE values> --values braintrust/examples/gke-loop-runtime/values.yaml --wait
   ```

Worker `replicas` is a floor; the controller scales from demand and usable capacity
within `controller.maxReplicas` and the Terraform node-pool maximum. Reserve an
extra worker for upgrades; requests can return `CapacityExhausted` while scaling
or at those limits.

## Filesystem retention

Retention is off unless the overlay is applied. Keep the worker chart, worker image
and Loop Runtime image compatible. Terraform selects the worker package from
`loopRuntime.sandboxServiceChart` in the chosen Braintrust Helm release.

Apply the GCP checkpoint-storage update first, then copy
`gke_isolated_workers.checkpoint_store` into `sandbox-service-retention.yaml`.
Only worker nodes receive checkpoint-bucket access through their node identity.

Apply the overlay using the same published worker chart and image as above:

```sh
helm --kube-context <worker> upgrade --install sandbox-service \
  https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/062fbf5e1090e1f624eda6d6f9dfe7af05921e3d90a6405d330feb7866775ad2/sandbox-service-0.1.0.tgz \
  --namespace braintrust-sandbox \
  --values braintrust/examples/gke-loop-runtime/sandbox-service.yaml \
  --values braintrust/examples/gke-loop-runtime/sandbox-service-retention.yaml --wait
```

With `--reuse-values`, explicitly set both retention values and a compatible image.
The controller owns scaling and draining; retain its eviction protections.

Upgrade replacements default to a 15-minute drain deadline; expiry can lose active
edits or local-only checkpoints. Change `SANDBOX_SERVICE_WORKER_DRAIN_TIMEOUT_SECONDS`
in `controller.env`, or use `0` to wait indefinitely. Ordinary scale-down has no
such deadline.

Workers still use local SSD/XFS. Checkpoints preserve files at completed idle sleep
or preserving drain, excluding running processes and later edits. Allow local disk
headroom for checkpoints and active sandboxes, and verify file recovery across
sleep, wake, and worker replacement before relying on it.

Keep the bucket, URI and encryption key stable while retained sandboxes exist.
Coordinate rollback before disabling retention. Infrastructure removal retains
the bucket; import the original storage before re-enabling workers. See the
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
The HPA target also sets sandbox capacity headroom, with a 1% sandbox minimum.
HPA targets below 1% stay unchanged; disabled autoscaling keeps the sandbox target
at 50%. Worker and node-pool maximums still bound sandbox capacity.

Sandbox administration is disabled by default. Set `loopRuntime.enableSandboxAdmin: true`
to allow authorized sysadmins to inspect and terminate sandboxes.

To shut down, set the worker release to `replicas: 0`, wait for the workers to
drain, then disable `loopRuntime.enabled` and uninstall the worker release. Its
pre-delete hook refuses to uninstall until workers drain.
