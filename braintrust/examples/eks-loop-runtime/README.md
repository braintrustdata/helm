# Loop Runtime and Sandbox Service on EKS

This example installs Loop Runtime and Sandbox Service on an existing EKS
cluster. It uses two Helm releases: the Braintrust chart owns Loop Runtime and
the shared Sandbox Service chart owns workers and their drain controller.
The worker chart is the released artifact, rather than another copy of its
templates in this repository.

The VPC, EKS cluster, node pools, IAM, database, Redis, and buckets remain owned
by your existing infrastructure configuration. Reuse its outputs as chart
inputs. An existing Terraform installer can install both releases with
`helm_release`; it does not need another implementation of those resources.

## Infrastructure inputs

| Input | Requirement |
| --- | --- |
| Cluster | Kubernetes 1.30 or newer, with permission to install the worker chart's admission policy and controller RBAC |
| Worker release | One per cluster; the shared chart uses fixed names for its cluster-scoped RBAC and admission policy |
| System nodes | Label `braintrust.io/workload=sandbox-system`; run Loop Runtime and the worker controller here |
| Worker nodes | Dedicated Linux nodes labeled `braintrust.io/workload=sandbox-service` and tainted `braintrust.io/sandbox=true:NoSchedule`, with working `/dev/kvm`, cgroup v2, host networking, and mount propagation |
| Worker disk | Prepare `/var/lib/braintrust-sandboxes/1` on node-local storage before scheduling workers; preserve it across worker restarts |
| Workload identities | Bind API, Brainstore, and Loop Runtime ServiceAccounts to their respective AWS permissions using IRSA or EKS Pod Identity |
| Data services | Set the existing chart's bucket names and `braintrust-secrets`; Loop uses the same database, Redis, and Brainstore storage |
| Network | Allow the runtime and controller to reach worker node addresses on TCP 9400; allow workers to reach the runtime egress Service on TCP 4002 |

Worker nodes need privileged containers, host PID/network namespaces, and KVM;
Fargate cannot run them. Keep workers on their dedicated nodes and keep
application credentials off those nodes. Apply network isolation at the node
and VPC layers: the worker pods use host networking, so a pod NetworkPolicy
alone does not establish the sandbox boundary. Nodes also need the image-pull
and DNS connectivity required by your infrastructure configuration.

The headless worker Service resolves directly to worker node addresses. With
both releases in this cluster, cluster DNS provides discovery and the runtime
egress Service provides Git proxy connectivity. No external DNS controller or
additional egress load balancer is required for this example.

## TLS and namespaces

Create the namespaces before installing either release:

```sh
kubectl create namespace braintrust
kubectl create namespace braintrust-sandbox
```

Use your PKI to issue a server certificate with the DNS SAN
`sandbox-service.braintrust-sandbox.svc.cluster.local` and server authentication
usage, and a client certificate with client authentication usage. The server
must trust the client CA, and the runtime/controller must trust the server CA.
Keep the server private key exclusively in the worker namespace.

The following commands use local PEM files, not certificate values in Helm or
Terraform configuration:

```sh
kubectl -n braintrust-sandbox create secret generic sandbox-tls \
  --from-file=cert.pem=server.pem \
  --from-file=key.pem=server.key \
  --from-file=ca_cert.pem=client-ca.pem

kubectl -n braintrust-sandbox create secret generic sandbox-client-tls \
  --from-file=client.pem=client.pem \
  --from-file=client.key=client.key \
  --from-file=server-ca.pem=server-ca.pem

kubectl -n braintrust create secret generic sandbox-client-tls \
  --from-file=client.pem=client.pem \
  --from-file=client.key=client.key \
  --from-file=server-ca.pem=server-ca.pem
```

## Install

These values use the immutable worker chart distributed with Braintrust
v2.18.0. Pin chart and image versions together when adopting a newer release.
From the root of this repository:

```sh
SANDBOX_CHART_URL=https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/cd27a3983e304932a7e1617adf3105c2b7320a340f26bfea577df781827142f9/sandbox-service-0.1.0.tgz

helm upgrade --install sandbox-service "$SANDBOX_CHART_URL" \
  --namespace braintrust-sandbox \
  --values braintrust/examples/eks-loop-runtime/sandbox-service.yaml \
  --wait

helm upgrade --install braintrust ./braintrust \
  --namespace braintrust \
  --values infrastructure-values.yaml \
  --values braintrust/examples/eks-loop-runtime/values.yaml \
  --wait
```

`infrastructure-values.yaml` is your existing chart configuration for buckets,
API/Brainstore identities, and storage. Add the Loop Runtime role to
`loopRuntime.serviceAccount.awsRoleArn` when using IRSA, or associate its
ServiceAccount with EKS Pod Identity. Set `AWS_REGION` through
`loopRuntime.extraEnvVars` if your identity integration does not inject it.

The example fixes worker capacity at one worker. Increase `replicas` in the
worker values according to available node capacity and the runtime's requested
sandbox sizes. Leave the worker chart's ECS-specific `loopRuntime.cluster` and
`loopRuntime.service` empty. Node scaling is handled by the existing cluster
node provisioner; allow capacity for an extra worker during rollouts.

## Upgrade and disable

Upgrade each release with the same values and namespace. The worker controller
owns replica changes and drains workers before replacing them; the StatefulSet
uses `OnDelete`, and deletion admission and a disruption budget prevent ordinary
pod deletion or node eviction from bypassing draining. Do not force-delete
worker pods or remove their nodes while sandboxes remain on them.

To shut down, stop new Loop work and finish or terminate existing sessions while
the runtime is still available to release their sandboxes. Keep the worker
release and TLS Secrets installed. Upgrade the worker release with `replicas: 0` and inspect
`kubectl -n braintrust-sandbox get configmap sandbox-rollout -o yaml` while it
drains. Once draining completes, disable `loopRuntime.enabled` in the Braintrust
release and uninstall the worker release. Its pre-delete
hook refuses uninstall while workers are not drained. Keep node pools and TLS
Secrets until that uninstall succeeds.

Workers hosted outside this cluster remain supported: supply their discovery
URL and TLS Secret, then expose the runtime egress Service through a reachable
internal endpoint as described in the chart README.
