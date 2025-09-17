# Braintrust Helm Repository

This repository contains the official Helm chart for deploying Braintrust's self-hosted data plane services to Kubernetes.

## Quick Start

### Install from OCI Registry

```bash
helm install braintrust oci://public.ecr.aws/braintrust/helm/braintrust
```

To install a specific version:

```bash
helm install braintrust oci://public.ecr.aws/braintrust/helm/braintrust --version 1.2.3
```

## Prerequisites

Before installing the Braintrust Helm chart, ensure you have run the appropriate braintrust terraform module [Google](https://github.com/braintrustdata/terraform-google-braintrust-data-plane) or [Azure](https://github.com/braintrustdata/terraform-azure-braintrust-data-plane) to deploy the base infrastructure.

See the [Braintrust Helm Chart](./braintrust/README.md) for more details.
