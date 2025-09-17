# Braintrust Helm Chart

## Prerequisites

This helm chart requires a Kubernetes secret named `braintrust-secrets` to exist in the namespace where the chart is installed. Azure users can optionally use the Azure Key Vault CSI driver to automatically sync secrets from Azure Key Vault into Kubernetes (see below for details).

## Required Secrets

The `braintrust-secrets` secret must contain the following keys:

| Secret Key | Description | Format |
|------------|-------------|--------|
| `REDIS_URL` | Redis connection URL | `redis://<host>:<port>` |
| `PG_URL` | PostgreSQL connection URL | `postgres://<username>:<password>@<host>:<port>/<database>` (append `?sslmode=require` if using TLS) |
| `BRAINSTORE_LICENSE_KEY` | Brainstore license key | Valid Brainstore license key from the Braintrust Data Plane settings page |
| `FUNCTION_SECRET_KEY` | Random string for encrypting function secrets | Random string |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure storage connection string | Valid Azure storage connection string (only required if `cloud` is `azure`) |
| `GCS_ACCESS_KEY_ID` | Google HMAC Access ID string | Valid S3 API Key Id (only required if `cloud` is `google`) |
| `GCS_SECRET_ACCESS_KEY` | Google HMAC Secret string | Valid S3 Secret string (only required if `cloud` is `google`) |

## Azure Key Vault CSI Integration (Optional)

If you're using Azure, you can optionally use the Azure Key Vault CSI driver to automatically sync secrets from Azure Key Vault into Kubernetes. This eliminates the need to manually create and manage the `braintrust-secrets` Kubernetes secret.

To enable this feature:

1. Set `azureKeyVaultCSI.enabled: true` in your values.yaml
2. Configure your Key Vault details:

   ```yaml
   azureKeyVaultCSI:
     enabled: true
     name: "your-keyvault-name"
     userAssignedIdentityID: "your-identity-id"
     clientID: "your-client-id"
     tenantId: "your-tenant-id"
   ```

3. Optionally map your Key Vault secret names to the required Kubernetes secret keys. This is only required if you aren't using our terraform module. The defaults assume you are using the Braintrust terraform module to deploy the base infrastructure.

   ```yaml
   secrets:
     - keyVaultSecretName: "your-redis-secret-name"
       kubernetesSecretKey: "REDIS_URL"
       keyVaultSecretType: "secret"
     # ... other secret mappings
   ```

The CSI driver will:

1. Mount the secrets from Key Vault into your pods
2. Automatically sync them to the `braintrust-secrets` Kubernetes secret
3. Keep the secrets in sync as they change in Key Vault

## Notes

- The `AZURE_STORAGE_CONNECTION_STRING` may or may not contain an AccountKey or SAS token depending on the storage account configuration. If a key or token is not provided, workload identity will be used.
- When using Azure Key Vault CSI, ensure your AKS cluster has the CSI driver installed and the managed identity has the correct permissions in Key Vault.

## Breaking Changes

With version 2 of this helm, the Brainstore pods are split into Readers and Writers improving performance and the ability to independently scale for more read operations or write operations. For existing customers that have deployed our Helm or via other means on Kubernetes, please update your override values file or deployment to match this change. This will result in no data loss, but will be a brief downtime as the existing Brainstore Pods are removed and new Brainstore Pods for Reading and Writing are launched.
