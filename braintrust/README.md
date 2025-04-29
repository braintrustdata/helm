# Braintrust Helm Chart

## Prerequisites

This helm chart requires a Kubernetes secret named `braintrust-secrets` to exist in the namespace where the chart is installed.

## Required Secrets

The `braintrust-secrets` secret must contain the following keys:

| Secret Key | Description | Format |
|------------|-------------|--------|
| `REDIS_URL` | Redis connection URL | `redis://<host>:<port>` |
| `PG_URL` | PostgreSQL connection URL | `postgres://<username>:<password>@<host>:<port>/<database>` |
| `BRAINSTORE_LICENSE_KEY` | Brainstore license key | Valid Brainstore license key from the Braintrust Data Plane settings page |
| `FUNCTION_SECRET_KEY` | Random string for encrypting function secrets | Random string |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure storage connection string | Valid Azure storage connection string (only required if `cloud` is `azure`) |

## Notes

- The `AZURE_STORAGE_CONNECTION_STRING` may or may not contain an AccountKey or SAS token depending on the storage account configuration. If a key or token is not provided, workload identity will be used.
