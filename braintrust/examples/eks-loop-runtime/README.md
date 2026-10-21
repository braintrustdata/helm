# Loop Runtime and Sandbox Service on EKS

Install two Helm releases: this chart for Loop Runtime and the published
Sandbox Service chart for workers and their drain controller. Reuse the
existing AWS networking, database, Redis, buckets, and IAM configuration.

## Prerequisites

- Kubernetes 1.30+; one worker release per cluster because its cluster-scoped
  RBAC and admission policy names are fixed.
- System nodes labeled `braintrust.io/workload=sandbox-system`.
- Dedicated Linux worker nodes labeled `braintrust.io/workload=sandbox-service`
  and tainted `braintrust.io/sandbox=true:NoSchedule`, with `/dev/kvm`,
  cgroup v2, mount propagation, and node-local storage prepared at
  `/var/lib/braintrust-sandboxes/1`. Workers require privileged containers
  and host PID/network namespaces; Fargate cannot run them.
- Node/VPC isolation for workers; pod NetworkPolicy alone does not isolate
  host-networked pods. Keep application credentials off worker nodes.
- Runtime/controller connectivity to worker node addresses on TCP 9400,
  worker connectivity to the runtime proxy Service on TCP 4002, and image-pull/DNS access.
- Your existing chart values and `braintrust-secrets`, plus Loop Runtime's
  workload identity. Set `loopRuntime.serviceAccount.awsRoleArn` for IRSA or
  associate its ServiceAccount with EKS Pod Identity. Supply `AWS_REGION`
  through `loopRuntime.extraEnvVars` if identity integration does not inject it.

Create namespaces `braintrust` and `braintrust-sandbox`, then these Secrets
from your PKI. The server certificate must cover
`sandbox-service.braintrust-sandbox.svc.cluster.local` and have server
authentication usage; client certificates need client authentication usage.

| Namespace / Secret | Keys |
| --- | --- |
| `braintrust-sandbox/sandbox-tls` | `cert.pem`, `key.pem`, `ca_cert.pem` (client CA) |
| `braintrust-sandbox/sandbox-client-tls` | `client.pem`, `client.key`, `server-ca.pem` |
| `braintrust/sandbox-client-tls` | `client.pem`, `client.key`, `server-ca.pem` |

Keep the server private key in the worker namespace. The server trusts the
client CA; the runtime and controller trust the server CA.

## Install

From the repository root, using the worker chart distributed with v2.18.0:

```sh
SANDBOX_CHART_URL=https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/cd27a3983e304932a7e1617adf3105c2b7320a340f26bfea577df781827142f9/sandbox-service-0.1.0.tgz

helm upgrade --install sandbox-service "$SANDBOX_CHART_URL" \
  --namespace braintrust-sandbox \
  --values braintrust/examples/eks-loop-runtime/sandbox-service.yaml --wait

helm upgrade --install braintrust ./braintrust \
  --namespace braintrust \
  --values infrastructure-values.yaml \
  --values braintrust/examples/eks-loop-runtime/values.yaml --wait
```

`infrastructure-values.yaml` supplies your existing bucket, identity, and
storage settings. Cluster DNS discovers workers; an internal Service exposes
the Git proxy. External workers can instead use an explicit discovery/proxy URL.
The worker example uses fixed capacity (`replicas: 1`); leave its ECS-specific
`loopRuntime.cluster/service` unset. Use the cluster's existing node
provisioner and allow an extra worker during rollouts. Pin chart/image versions together.

## Upgrade and shutdown

Upgrade with the same values and namespaces. The worker controller owns
scale-down and replacement; do not force-delete workers or remove their nodes.
Before shutdown, stop new work and finish/terminate sessions while the runtime
can release their sandboxes. Upgrade the worker release with `replicas: 0`
and inspect `kubectl -n braintrust-sandbox get configmap sandbox-rollout -o yaml`.
After draining, disable `loopRuntime.enabled` and uninstall the worker release.
Its pre-delete hook refuses uninstall until drained; retain nodes and TLS
Secrets until it succeeds.
