## Aws Helm chart usage exxample

Template debug AWS Full Configuration Example for Braintrust Helm Chart
This example shows all available configuration options for AWS deployment

```bash
cd examples/aws
helm dependency build
helm template aws-test . > output.yaml
```