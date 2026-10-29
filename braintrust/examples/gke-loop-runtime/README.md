# Loop Runtime on GKE

Loop Runtime runs in the primary GKE cluster. Its sandboxes run on Sandbox
Service workers in a separate isolated cluster. Both clusters come from the
GCP Terraform module with:

```hcl
gke_isolated_workers = {
  network_cidr = "10.64.0.0/16" # must not overlap existing ranges
  discovery    = { dns_name = "sandbox.<deployment>.internal" }
}
```

`<...>` values below come from `terraform output gke_isolated_workers` and
`terraform output loop_runtime_service_account`.

Use compatible API, Brainstore, and AI Gateway images for the runtime build.
The API must report `ff_version` 82 or newer, and the Rust gateway must support
`x-bt-provider-model-map`; the chart's default API image `v2.18.0` is too old.
Deploy the gateway with `aiGateway.enabled=true` first and wait for it to become
ready before applying this example, which turns on `aiGateway.useGateway`.
The TypeScript API proxy alone cannot restore the provider model IDs sent by Codex.

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

Each runtime pod claims at most two conversations and has a 256Mi in-memory cache budget.
A two-turn GKE smoke run using Git, SQL, and shell tools peaked at 329MiB; the pod retains a 2Gi limit.
Sandbox administration is disabled by default. Set `loopRuntime.enableSandboxAdmin: true`
to allow authorized sysadmins to inspect and terminate sandboxes.

To shut down, set the worker release to `replicas: 0`, wait for the workers to
drain, then disable `loopRuntime.enabled` and uninstall the worker release. Its
pre-delete hook refuses to uninstall until workers drain.
