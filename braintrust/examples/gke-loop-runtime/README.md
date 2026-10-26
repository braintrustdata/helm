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

1. Prepare worker nodes (mounts XFS directly at the worker's state path):

   ```sh
   kubectl --context <worker> create namespace braintrust-sandbox
   kubectl --context <worker> apply -f braintrust/examples/gke-loop-runtime/node-init.yaml
   ```

2. Create the mTLS Secrets in both clusters:

   ```sh
   braintrust/examples/gke-loop-runtime/create-sandbox-tls.sh "<discovery.hostname>" <worker> <primary>
   ```

3. Install the workers:

   ```sh
   helm --kube-context <worker> upgrade --install sandbox-service \
     https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/cd27a3983e304932a7e1617adf3105c2b7320a340f26bfea577df781827142f9/sandbox-service-0.1.0.tgz \
     --namespace braintrust-sandbox --values braintrust/examples/gke-loop-runtime/sandbox-service.yaml --wait
   ```

4. Install this chart with your GKE values plus `values.yaml` from this directory:

   ```sh
   helm --kube-context <primary> upgrade --install braintrust ./braintrust --namespace braintrust \
     --values <your GKE values> --values braintrust/examples/gke-loop-runtime/values.yaml --wait
   ```

Workers run at a fixed count. One `c4-standard-8-lssd` worker holds about seven
1 vCPU / 2 GiB sandboxes; beyond that, new sandboxes fail with
`CapacityExhausted`. Raise `replicas` in `sandbox-service.yaml` to add workers.

To shut down, set the worker release to `replicas: 0`, wait for the workers to
drain, then disable `loopRuntime.enabled` and uninstall the worker release. Its
pre-delete hook refuses to uninstall until workers drain.
