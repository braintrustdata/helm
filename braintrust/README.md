This helm chart assumes a Kubernetes secret named `braintrust-secrets` exists in the namespace where the chart is installed.

The secret must contain the following keys:

- `REDIS_URL`
- `PG_URL`
- `BRAINSTORE_LICENSE_KEY`
- `FUNCTION_SECRET_KEY`
- `AZURE_STORAGE_CONNECTION_STRING` (only if `cloud` is `azure`)

The `REDIS_URL` must be in the format `redis://<host>:<port>`.

The `PG_URL` must be in the format `postgres://<username>:<password>@<host>:<port>/<database>`.

The `BRAINSTORE_LICENSE_KEY` must be a valid Brainstore license key.

The `FUNCTION_SECRET_KEY` a random string for encrypting function secrets.

The `AZURE_STORAGE_CONNECTION_STRING` must be a valid Azure storage connection string. This may or may not contain a secret key depending on the storage account configuration. If not, workload identity will be tried.
