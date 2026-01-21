# Testing the Braintrust Helm Chart

This document describes how to test the Braintrust Helm chart locally and in CI.

## Prerequisites

### Install helm-unittest

`helm-unittest` is a Helm plugin for unit testing templates. Install it with:

```bash
helm plugin install https://github.com/quintush/helm-unittest
```

Verify installation:
```bash
helm unittest --version
```

### Install Chart Testing (ct)

`ct` is a tool for linting and testing Helm charts. Install it with:

**macOS:**
```bash
brew install chart-testing
```

**Linux:**
```bash
# Download the latest release
wget https://github.com/helm/chart-testing/releases/latest/download/ct_linux_amd64.tar.gz
tar -xzf ct_linux_amd64.tar.gz
sudo mv ct /usr/local/bin/
```

**Verify installation:**
```bash
ct version
```

## Running Tests Locally

### Run All Tests

Use the provided test script to run all tests:

```bash
./test.sh
```

This will:
1. Run helm-unittest on all unit test files
2. Run Chart Testing (lint, validate, test)

### Run Unit Tests Only

Run only the unit tests (fast, no cluster needed):

```bash
helm unittest braintrust
```

Run unit tests for a specific file:

```bash
helm unittest braintrust/tests/storageclass_test.yaml
```

### Run Chart Testing Only

Run linting and validation:

```bash
ct lint --charts braintrust
```

Run with specific values files:

```bash
ct lint --charts braintrust --validate-maintainers=false
```

## Test Structure

### Unit Tests

Unit tests are located in `braintrust/tests/` and use the `helm-unittest` format. Each test file corresponds to a template file and tests:

- Template rendering with different values
- Conditional logic (cloud provider, feature flags)
- Helper functions
- Value merging and defaults

### Integration Tests

Integration tests use Chart Testing and validate:

- YAML syntax correctness
- Kubernetes schema validation
- Chart linting (best practices)
- Rendering with different values files

Test values files are in `braintrust/ci/`:
- `values-azure.yaml` - Azure-specific configuration
- `values-google.yaml` - Google Cloud-specific configuration
- `values-aws.yaml` - AWS-specific configuration
- `values-minimal.yaml` - Minimal required values

## Adding New Tests

### Adding Unit Tests

1. Create a test file in `braintrust/tests/` matching the template name (e.g., `my-template_test.yaml`)
2. Write test cases using the helm-unittest syntax
3. Run tests: `helm unittest braintrust`

Example test structure:
```yaml
suite: test my template
templates:
  - my-template.yaml
tests:
  - it: should render correctly
    values:
      myValue: "test"
    asserts:
      - matchRegex:
          path: metadata.name
          pattern: ^my-resource
```

### Adding Integration Test Values

1. Create a new values file in `braintrust/ci/` (e.g., `values-custom.yaml`)
2. The file will be automatically picked up by Chart Testing
3. Ensure it includes all required values to render the chart

## CI Integration

Tests run automatically in GitHub Actions on:
- Every pull request
- Every push to main/master branches

The CI workflow:
1. Installs helm-unittest and ct
2. Runs all unit tests
3. Runs Chart Testing (lint, validate)
4. Tests with multiple values files

## Troubleshooting

### Unit Tests Fail

- Check that your test values match the template expectations
- Verify template paths in assertions are correct
- Use `helm template` to manually render and inspect output

### Chart Testing Fails

- Ensure all required values are provided in test values files
- Check for YAML syntax errors
- Verify Kubernetes API versions are correct

### Local Validation

You can also use the existing `validate.sh` script for manual validation against a Kubernetes cluster:

```bash
./validate.sh
```

This requires a running Kubernetes cluster and kubectl access.
