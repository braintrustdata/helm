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
| `REDIS_CA_PEM` | Custom Redis TLS CA bundle | Full PEM bundle as a multiline string (BEGIN/END blocks). Only required if `customRedisTLSCABundle: true`. |
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

## GKE with Local SSDs

Braintrust requires local SSDs for maximum disk performance. Configuration varies depending on whether you're using GKE Autopilot or Standard mode.

### GKE Autopilot

For Autopilot clusters, simply set the mode and the chart will automatically configure local SSDs:

```yaml
cloud: "google"

google:
  mode: "autopilot"
  autopilotMachineFamily: "c4"  # Machine family that supports local SSDs

brainstore:
  reader:
    volume:
      size: "375Gi"  # Local SSDs come in 375Gi increments (375, 750, 1125, etc.)
    resources:
      requests:
        cpu: "8"
        memory: "16Gi"
  writer:
    volume:
      size: "375Gi"
    resources:
      requests:
        cpu: "32"
        memory: "64Gi"
```

**What happens:**
- Autopilot automatically provisions C4 nodes with local SSDs
- Node selectors are added automatically (including `compute-class: Performance` for dedicated nodes)
- Ephemeral-storage requests ensure proper SSD allocation
- Each brainstore pod gets its own dedicated node with full access to local SSDs

**Supported machine families:** c4, c4d,

### GKE Standard Mode

For Standard mode clusters, create node pools with local SSDs, then deploy:

**Configure the Helm chart:**
   ```yaml
   cloud: "google"

   google:
     mode: "standard" 

   brainstore:
     reader:
       nodeSelector:
         cloud.google.com/gke-nodepool: "brainstore"  # Target your node pool
       resources:
         requests:
           cpu: "44"
           memory: "160Gi"
       # Prevent readers and writers from sharing nodes
       affinity:
         podAntiAffinity:
           requiredDuringSchedulingIgnoredDuringExecution:
             - labelSelector:
                 matchExpressions:
                   - key: app
                     operator: In
                     values:
                       - brainstore-reader
                       - brainstore-writer
               topologyKey: kubernetes.io/hostname
     writer:
       nodeSelector:
         cloud.google.com/gke-nodepool: "brainstore"
       resources:
         requests:
           cpu: "44"
           memory: "160Gi"
       # Prevent readers and writers from sharing nodes
       affinity:
         podAntiAffinity:
           requiredDuringSchedulingIgnoredDuringExecution:
             - labelSelector:
                 matchExpressions:
                   - key: app
                     operator: In
                     values:
                       - brainstore-reader
                       - brainstore-writer
               topologyKey: kubernetes.io/hostname
   ```

**What happens:**
- Pods are scheduled on your pre-configured node pools
- Local SSDs are automatically available via emptyDir volumes
- Pod anti-affinity ensures readers and writers don't share nodes (each pod gets dedicated node access)

## Notes

- The `AZURE_STORAGE_CONNECTION_STRING` may or may not contain an AccountKey or SAS token depending on the storage account configuration. If a key or token is not provided, workload identity will be used.
- When using Azure Key Vault CSI, ensure your AKS cluster has the CSI driver installed and the managed identity has the correct permissions in Key Vault.

## Breaking Changes

With version 2 of this helm, the Brainstore pods are split into Readers and Writers improving performance and the ability to independently scale for more read operations or write operations. For existing customers that have deployed our Helm or via other means on Kubernetes, please update your override values file or deployment to match this change. This will result in no data loss, but will be a brief downtime as the existing Brainstore Pods are removed and new Brainstore Pods for Reading and Writing are launched.
