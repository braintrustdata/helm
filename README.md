# Braintrust Helm Repository

For the latest guidance, always refer to the official Braintrust documentation:
- [Self-hosting overview](https://www.braintrust.dev/docs/admin/self-hosting)
- [Data Plane 2.0 upgrade guide](https://www.braintrust.dev/docs/admin/self-hosting/upgrade/v2)

This repository contains the official Helm chart for deploying Braintrust's self-hosted data plane services to Kubernetes.

## Quick Start

### Install from OCI Registry

```bash
helm upgrade --install \
  --namespace braintrust --create-namespace \
  braintrust \
  oci://public.ecr.aws/braintrust/helm/braintrust \
  --version 1.2.3 \
  --values helm-values.yaml
```

## Prerequisites

Before installing the Braintrust Helm chart, ensure you have run the appropriate braintrust terraform module [Google](https://github.com/braintrustdata/terraform-google-braintrust-data-plane) or [Azure](https://github.com/braintrustdata/terraform-azure-braintrust-data-plane) to deploy the base infrastructure.

See the [Braintrust Helm Chart](./braintrust/README.md) for more details.

## GKE deployment modes

GKE Autopilot is the preferred solution for new Braintrust deployments. GKE Standard is supported when customer requirements prevent Autopilot use.

## GKE Standard node pool changes

The [Standard example](braintrust/examples/google-standard/values.yaml) uses stable workload selectors and enables optional API and Brainstore disruption budgets.
The chart defaults remain unchanged for other deployments.
The [disruption budget guidance](braintrust/README.md#optional-disruption-budgets) explains the single-writer exception and the preparation steps for existing GKE pools.
