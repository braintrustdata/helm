# AI Agent Instructions

This document provides guidelines for AI agents reviewing or modifying this Helm chart repository.

## Testing Requirements

### Running Tests

```bash
./test.sh
```

This runs:
1. Unit tests via `helm-unittest`
2. Template rendering tests for all cloud providers
3. Helm lint

### When to Add Tests

- Any new template that uses label merging must have corresponding isolation tests
- New features should have unit test coverage
- Test fixtures are in `braintrust/tests/__fixtures__/`

### Test File Naming

- Template tests: `<template-name>_test.yaml`
- Cross-template tests: `<toplic>-*_test.yaml`

## Template Guidelines

### Cloud Provider Conditionals

This chart supports multiple cloud providers. Use conditionals appropriately:

```yaml
{{- if eq .Values.cloud "google" }}
# Google-specific configuration
{{- end }}

{{- if eq .Values.cloud "azure" }}
# Azure-specific configuration
{{- end }}

{{- if eq .Values.cloud "aws" }}
# AWS-specific configuration
{{- end }}
```

### Required Values

Use `required` for values that must be set for specific configurations:

```yaml
{{ required "brainstore.serviceAccount.googleServiceAccount is required when cloud is google" .Values.brainstore.serviceAccount.googleServiceAccount }}
```

### Namespace Handling

Always use the namespace helper:

```yaml
namespace: {{ include "braintrust.namespace" . }}
```

## GKE mode recommendation

GKE Autopilot is the preferred solution for new Braintrust deployments.
Standard is supported when customer requirements prevent Autopilot use.
The separate Standard example does not change this preference.
Existing deployments must keep their initial cluster mode.
A mode change replaces the cluster, causes downtime, and requires Helm release redeployment.

Recommend Autopilot first for new deployments.
Present Standard as a supported alternative for customer constraints.
Do not imply that existing Standard customers must switch modes.

## Critical Safety Constraints

These constraints exist because of real incidents and confirmed engineering guidance. Do not "simplify" or "clean up" code that implements them.

### Upgrade Sequencing (for customers upgrading from pre-2.0)

These constraints apply to customers migrating from Data Plane 1.x to 2.0. New deployments on 2.0+ ship with WAL v3 and no-PG defaults baked in.

- **Never set `skipPgForBrainstoreObjects` on Data Plane versions before 2.0.** A known bug on 1.1.32 was hit by a customer and fixed in the 2.0 images. The correct upgrade sequence is: 1.1.32 -> WAL v1 -> 2.0 + WAL v3 -> no-PG.
- **Never set `brainstoreWalFooterVersion` in the same deploy as an image version bump.** Old Brainstore nodes still rolling out cannot read the new WAL format. Exception: bumping v1 to v3 can be done in the same deploy as the 2.0 image upgrade because all 2.0 nodes understand v3.
- **`skipPgForBrainstoreObjects` is a one-way operation.** Once enabled for an object type, it cannot be rolled back without downtime.

### WAL_USE_EFFICIENT_FORMAT Decoupling

`BRAINSTORE_WAL_USE_EFFICIENT_FORMAT` is intentionally derived from EITHER `brainstoreWalFooterVersion` OR `skipPgForBrainstoreObjects` being set. This is not redundant - it enables efficient format as early as possible in the upgrade sequence (when WAL v1 is set) rather than waiting for no-PG. Do not "simplify" this to only check one condition.

### Brainstore ConfigMap Consistency

The three brainstore configmaps (`brainstore-reader-configmap.yaml`, `brainstore-writer-configmap.yaml`, `brainstore-fastreader-configmap.yaml`) must have identical environment variable logic for `BRAINSTORE_RESPONSE_CACHE_URI`, `BRAINSTORE_CODE_BUNDLE_URI`, `BRAINSTORE_ASYNC_SCORING_OBJECTS`, and `BRAINSTORE_LOG_AUTOMATIONS_OBJECTS`. If you modify one, you must update all three.

### Disruption budgets and GKE Standard

Brainstore readers, fast readers, and writers each expose an optional `podDisruptionBudget`.
Budgets default to disabled on every cloud. An enabled Brainstore budget defaults to `maxUnavailable: 1`.
The API retains `minAvailable` unless an explicit `maxUnavailable` takes precedence.
The GKE Standard example enables separate budgets for the API and all Brainstore roles.
A single writer can stop briefly during eviction. With multiple writers, the budget permits one unavailable replica.
Each role has an independent budget, so different roles can lose a replica simultaneously.
Readiness probes determine healthy replicas. Deployment rollout settings and `minReadySeconds` do not control node eviction.
Stable `braintrust/node-pool` labels connect Helm selectors to Terraform pools.
The Terraform pool map key sets this label. Automatic replacement preserves the label.
GKE deletion protection must exist on the source pool before hardware replacement. Its PDB protection expires after one hour.

- Keep PDB templates cloud-independent and disabled by default.
- Enable the budgets in the GKE Standard example.
- Preserve the single-writer interruption exception.
- Use stable workload labels instead of generated GKE pool names in selectors.
- Document the Helm deployment and source pool protection steps before hardware replacement.
- Do not describe PDBs as an unconditional zero-downtime guarantee.
- Test role isolation, API compatibility, and cross-cloud behavior after PDB changes.

### Version Numbers

Chart version numbers are semantically meaningful for the upgrade path:
- Minor versions (e.g., 5.2.0) are additive, non-breaking chart features
- Major versions (e.g., 6.0.0) carry Data Plane image tag changes that affect runtime behavior

Do not bump `Chart.yaml` version without explicit coordination - version numbers determine customer upgrade sequencing.

## Review guidelines

When reviewing PRs, verify:

- [ ] Any `merge` on dicts uses `deepCopy` wrapper to ensure the original dict is not mutated
- [ ] New templates follow existing patterns
- [ ] Tests are added for new functionality
- [ ] Cloud-specific code is properly conditioned

## File Structure

```
braintrust/
├── templates/           # Kubernetes manifest templates
├── tests/              # helm-unittest test files
│   └── __fixtures__/   # Test value fixtures
├── ci/                 # CI value files for different clouds
├── values.yaml         # Default values
└── Chart.yaml          # Chart metadata
```
