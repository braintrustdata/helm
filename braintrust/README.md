# Braintrust Helm Chart

## Prerequisites

This helm chart requires a Kubernetes secret named `braintrust-secrets` to exist in the namespace where the chart is installed. Azure users will automatically sync secrets from Azure Key Vault into Kubernetes (see below for details). AWS and Google users will need to manually create and manage the `braintrust-secrets` Kubernetes secret.

## Required Secrets

The `braintrust-secrets` secret must contain the following keys:

| Secret Key | Description | Format |
|------------|-------------|--------|
| `REDIS_URL` | Redis connection URL | `redis://<host>:<port>` |
| `PG_URL` | PostgreSQL connection URL | `postgres://<username>:<password>@<host>:<port>/<database>` (append `?sslmode=require` if using TLS) |
| `BRAINSTORE_LICENSE_KEY` | Brainstore license key | Valid Brainstore license key from the Braintrust Data Plane settings page |
| `FUNCTION_SECRET_KEY` | Random string for encrypting function secrets | Random string |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure storage connection string | Valid Azure storage connection string (only required if `cloud` is `azure`) |
| `GCS_ACCESS_KEY_ID` | Google HMAC Access ID string | Valid S3 API Key Id (only required if `cloud` is `google` and if `enableGcsAuth` is `false`) |
| `GCS_SECRET_ACCESS_KEY` | Google HMAC Secret string | Valid S3 Secret string (only required if `cloud` is `google` and if `enableGcsAuth` is `false`) |

## Azure Key Vault Driver Integration

If you're using Azure, the Azure Key Vault CSI driver is default enabled and will automatically sync secrets from Azure Key Vault into Kubernetes. This eliminates the need to manually create and manage the `braintrust-secrets` Kubernetes secret.

To enable this feature:

1. Configure your Key Vault details:

   ```yaml
   azure:
     keyVaultName: "your-keyvault-name"
     keyVaultCSIclientID: "your-client-id" # This should come from the terraform module
     tenantId: "your-tenant-id"
   ```

2. Optionally map your Key Vault secret names to the required Kubernetes secret keys. This is only required if you aren't using our terraform module. The defaults assume you are using the Braintrust terraform module to deploy the base infrastructure.

   ```yaml
   azureKeyVaultDriver:
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

## Optional: Enterprise CA bundle for user-code runtimes

If your organization uses private or enterprise CAs, you can provide a dedicated Secret for a CA bundle and configure the API container, plus user-code runtimes that inherit environment from it, to trust that bundle.

This feature:

1. Uses a separate Secret for the CA bundle.
2. Mounts only the configured Secret key via `items` (not the full Secret).
3. Does not modify files under the system certificate store; clients that honor the configured env vars will trust the mounted bundle instead of (or in addition to) system defaults.
4. Does not require root.
5. Sets standard CA bundle environment variables on the API container (`NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `AWS_CA_BUNDLE`, and `PIP_CERT`).

Example values:

```yaml
api:
  customCA:
    enabled: true
    secretName: braintrust-runtime-ca
    secretKey: ca-bundle.pem
    mountPath: /etc/braintrust/runtime-ca
    filename: ca-bundle.pem
```

Create the Secret before installing or upgrading the chart:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: braintrust-runtime-ca
type: Opaque
stringData:
  ca-bundle.pem: |
    -----BEGIN CERTIFICATE-----
    ...
    -----END CERTIFICATE-----
```

The configured key should contain a full CA bundle, not only your private CA chain. Several of the environment variables set by this feature cause clients to use the configured file as their CA bundle, so include the public root certificates your workloads still need along with your enterprise root and intermediate certificates.

`NODE_EXTRA_CA_CERTS` appends to the default trust store in Node.js, while `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` typically replace the default bundle for OpenSSL, Python, and curl clients. Because all of these variables point at the same mounted file, behavior can differ by runtime: a bundle with only your enterprise CA may work for Node.js user code but break Python or curl outbound HTTPS unless public roots are included.

Kubernetes limits each Secret to 1 MiB. A normal combined CA bundle is typically well below that limit, but very large enterprise bundles should be checked before rollout.

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

**Supported machine families:** c4, c4d

If you need the request to cover more than the cache volume alone, set an explicit total pod-local storage budget:

```yaml
brainstore:
  reader:
    volume:
      size: "900Gi"
    ephemeralStorage:
      request: "905Gi"  # cache + /tmp (if enabled) + logs/writable-layer overhead
```

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

## AWS EKS Local Storage

On EKS, Brainstore uses Kubernetes-managed `emptyDir` volumes for cache storage. To make scheduling reflect the real local-disk budget, set `brainstore.<role>.ephemeralStorage.request` for each Brainstore role.

Size the request for the pod's full local-storage usage:
- cache `emptyDir`
- optional `/tmp` `emptyDir`
- container logs
- writable layer overhead

When you enable `tmpVolume`, make sure the `ephemeralStorage.request` still covers that extra space.

## Testing

This Helm chart includes comprehensive automated unit tests.

```bash
# Run all tests
./test.sh
```

## Breaking Changes

### Version 2

With version 2 of this helm, the Brainstore pods are split into Readers and Writers improving performance and the ability to independently scale for more read operations or write operations. For existing customers that have deployed our Helm or via other means on Kubernetes, please update your override values file or deployment to match this change. This will result in no data loss, but will be a brief downtime as the existing Brainstore Pods are removed and new Brainstore Pods for Reading and Writing are launched.

### Version 3

Breaking change only for Azure customers which introduced the Azure Container Storage CSI driver.

### Version 4

This version of the Helm is in preparation of 2.0.0 of the Braintrust Self hosted Data Plane. Starting with 1.1.32 Brainstore will now need to reach out to the API, where before Brainstore didn't talk to the API. In Helm this is being done over the internal Kubernetes endpoint. If you have additional security restrictions or are limiting traffic between services, this will need to be allowed before upgrading to 2.0.0 of the data plane.

We are also increasing the default sizing of our deployments, please ensure you have the node pool capacity for these increased defaults.

### Version 5

This release adds new Brainstore Fast Readers and enables them by default. Fast readers are isolated Brainstore nodes that handle common and known safe queries that power the Braintrust UI. This effectively isolates resource intensive adhoc queries into the standard Brainstore readers nodes which helps to keep the UI responsive.
You may have to adjust your helm values.yaml overrides if you have adjusted any defaults for standard Brainstore reader nodes. We recommend keeping your fast readers sized the same as your existing readers and starting with only two nodes.

Also if you have custom readiness checks, please unset these customizations and use our new default readiness checks. There is a bug in the dataplane where the endpoint we were using for readiness checks, would never recover if it failed.

### Version 6

This version introduces opt-in no-PG mode, allowing Brainstore to store objects directly without PostgreSQL. The new `skipPgForBrainstoreObjects` value defaults to `""` (disabled), so upgrading makes no behavioral change unless you explicitly set it. This solves a longstanding bottleneck - the rate and volume of data ingestion is no longer limited by Postgres, and this means faster, more reliable data ingestion at higher scale.

> **WARNING: no-PG must only be enabled after upgrading to Data Plane 2.0 images.** A known bug on 1.1.32 was fixed in the 2.0 images. Do not enable no-PG on 1.1.32.

> **WARNING: This is a one-way operation.** Once an object type has been migrated off PostgreSQL, it cannot be un-migrated without downtime.

This version also adds first-class `brainstoreWalFooterVersion` support and auto-derived `BRAINSTORE_RESPONSE_CACHE_URI` / `BRAINSTORE_CODE_BUNDLE_URI` from existing `objectStorage` config.

## Example Values Files

Example values files for different cloud providers and configurations are located in the `examples/` folder.

- `examples/google-autopilot/values.yaml`: GKE Autopilot deployment.
- `examples/google-autopilot-cel/values.yaml`: GKE Autopilot deployment with CEL-friendly security settings.
- `examples/google-standard/values.yaml`: GKE Standard deployment.
