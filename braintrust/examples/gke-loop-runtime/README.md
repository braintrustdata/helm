# Loop Runtime on GKE

Set `enable_loop_runtime = true` in the
[GCP data-plane module](https://github.com/braintrustdata/terraform-google-braintrust-data-plane#loop-runtime-infrastructure)
and apply infrastructure first. Workers run in an isolated cluster; Loop Runtime
stays in the primary cluster with its existing Autopilot or Standard mode.

## Helm-managed primary release

Infrastructure installs workers, waits for readiness and persists separate server
and client CA bundles in Secret Manager for reuse. Pass its configuration after
your existing Helm values:

```sh
terraform output -raw loop_runtime_helm_values_yaml > loop-values.yaml
helm upgrade --install braintrust oci://public.ecr.aws/braintrust/helm/braintrust \
  --namespace braintrust --create-namespace --version <chart-version> \
  --reset-then-reuse-values --values <existing-values.yaml> \
  --values loop-values.yaml --wait
```

Use a chart supporting `loopRuntime.sandbox.clientTlsSecret`. The overlay supplies
the secret reference, runtime identity, worker version and connection settings;
keys stay out of Terraform state and Helm values. At Pod startup, Workload Identity
loads only the client bundle into memory for a read-only runtime mount, without
access to server credentials or CA keys. No setup root, worker providers or manual
TLS generation is required; use `loop_runtime_options` for worker overrides.

Deploy compatible API, Brainstore and routed AI Gateway versions first. Keep the
generated runtime/worker version compatible with those services. For a Standard
primary cluster, retain your `loopRuntime.nodeSelector` override for its services
pool. Explicit values after `--reset-then-reuse-values` replace retained settings.

## Terraform-managed primary release

Pass the same output to the
[GKE app module](https://github.com/braintrustdata/terraform-google-braintrust-gke-app#managed-loop-runtime):

```hcl
loop_runtime = try(data.terraform_remote_state.infra.outputs.loop_runtime, null)
```

Apply infrastructure before the app root; no app enable flag is needed.
See the [remote-state example](https://github.com/braintrustdata/terraform-google-braintrust-gke-app/tree/main/examples/remote-state-app-root);
direct Helm installations do not need the app module.

## Custom worker installations

These optional steps are for installations that manage the worker release and
TLS themselves. Ordinary Terraform and Helm deployments use the automatic path
above. Provision standalone workers with the GCP infrastructure module:

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

The automatic infrastructure bootstrap enables filesystem retention and supplies
the checkpoint store. The following overlay steps apply only to custom worker
installations. Keep the worker chart, worker image and Loop Runtime compatible.

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
