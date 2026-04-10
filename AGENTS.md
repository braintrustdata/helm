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

## Critical Safety Constraints

These constraints exist because of real incidents and confirmed engineering guidance. Do not "simplify" or "clean up" code that implements them.

### Upgrade Sequencing

- **Never set `skipPgForBrainstoreObjects` on Data Plane versions before 2.0.** A known bug on 1.1.32 was hit by a customer (Ramp) and fixed in the 2.0 images. The correct sequence is: 1.1.32 -> WAL v1 -> 2.0 + WAL v3 -> no-PG.
- **Never set `brainstoreWalFooterVersion` in the same deploy as an image version bump.** Old Brainstore nodes still rolling out cannot read the new WAL format. Exception: bumping v1 to v3 can be done in the same deploy as the 2.0 image upgrade because all 2.0 nodes understand v3.
- **`skipPgForBrainstoreObjects` is a one-way operation.** Once enabled for an object type, it cannot be rolled back without downtime. Do not default this to any non-empty value.

### WAL_USE_EFFICIENT_FORMAT Decoupling

`BRAINSTORE_WAL_USE_EFFICIENT_FORMAT` is intentionally derived from EITHER `brainstoreWalFooterVersion` OR `skipPgForBrainstoreObjects` being set. This is not redundant - it enables efficient format as early as possible in the upgrade sequence (when WAL v1 is set) rather than waiting for no-PG. Do not "simplify" this to only check one condition.

### Brainstore ConfigMap Consistency

The three brainstore configmaps (`brainstore-reader-configmap.yaml`, `brainstore-writer-configmap.yaml`, `brainstore-fastreader-configmap.yaml`) must have identical environment variable logic for `BRAINSTORE_RESPONSE_CACHE_URI`, `BRAINSTORE_CODE_BUNDLE_URI`, `BRAINSTORE_ASYNC_SCORING_OBJECTS`, and `BRAINSTORE_LOG_AUTOMATIONS_OBJECTS`. If you modify one, you must update all three.

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
