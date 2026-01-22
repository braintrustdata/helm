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
