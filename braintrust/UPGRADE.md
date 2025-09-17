# Upgrade Guide

This document outlines breaking changes and required configuration updates for major releases.

## v2.0.0 - Brainstore Reader/Writer Split

This release introduces a significant architectural change that splits the Brainstore service into separate reader and writer services for improved scalability and performance.

### Breaking Changes

#### 1. Brainstore Service Split

The single `brainstore` service has been split into two separate services:

- **brainstore-reader**: Handles read-only operations (`BRAINSTORE_READER_ONLY_MODE: true`)
- **brainstore-writer**: Handles write operations (`BRAINSTORE_READER_ONLY_MODE: false`)

#### 2. API Environment Variables

The API now uses separate URLs for read and write operations:

- **BRAINSTORE_URL**: Points to brainstore-reader service
- **BRAINSTORE_WRITER_URL**: Points to brainstore-writer service (new)

#### 3. Values.yaml Structure Changes

**Before (v1.x):**

```yaml
brainstore:
  enabled: true
  name: "brainstore"
  labels: {}
  annotations:
    serviceaccount: {}
    configmap: {}
    deployment: {}
    service: {}
    pod: {}
  replicas: 2
  image:
    repository: public.ecr.aws/braintrust/brainstore
    tag: v1.1.21
    pullPolicy: Always
  service:
    name: ""
    type: ClusterIP
    port: 4000
    portName: http
  serviceAccount:
    name: "brainstore"
    awsRoleArn: ""
    azureClientId: ""
    googleServiceAccount: ""
  resources:
    requests:
      cpu: "8"
      memory: "16Gi"
    limits:
      cpu: "16"
      memory: "32Gi"
  cacheDir: "/mnt/tmp/brainstore"
  objectStoreCacheMemoryLimit: "1Gi"
  objectStoreCacheFileSize: "50Gi"
  verbose: true
  extraEnvVars: []
```

**After (v2.0):**

```yaml
brainstore:
  # Shared configuration
  labels: {}
  serviceAccount:
    name: "brainstore"
    awsRoleArn: ""
    azureClientId: ""
    googleServiceAccount: ""
    annotations: {}  # MOVED: was brainstore.annotations.serviceaccount
  # Shared image configuration
  image:
    repository: public.ecr.aws/braintrust/brainstore
    tag: v1.1.21
    pullPolicy: IfNotPresent  # CHANGED: from Always

  # Brainstore Reader configuration
  reader:
    name: "brainstore-reader"
    labels: {}
    annotations:
      configmap: {}
      deployment: {}
      service: {}
      pod: {}
    replicas: 2  # CHANGED: can be scaled independently
    service:
      name: ""
      type: ClusterIP
      port: 4000
      portName: http
    resources:
      requests:
        cpu: "8"
        memory: "16Gi"
      limits:
        cpu: "16"
        memory: "32Gi"
    cacheDir: "/mnt/tmp/brainstore"
    cacheSizeLimit: "50Gi"  # NEW: required field
    objectStoreCacheMemoryLimit: "1Gi"
    objectStoreCacheFileSize: "50Gi"
    verbose: true
    extraEnvVars: []

  # Brainstore Writer configuration
  writer:
    name: "brainstore-writer"
    labels: {}
    annotations:
      configmap: {}
      deployment: {}
      service: {}
      pod: {}
    replicas: 1  # CHANGED: can be scaled independently
    service:
      name: ""
      type: ClusterIP
      port: 4000
      portName: http
    resources:
      requests:
        cpu: "8"
        memory: "16Gi"
      limits:
        cpu: "16"
        memory: "32Gi"
    cacheDir: "/mnt/tmp/brainstore"
    cacheSizeLimit: "50Gi"  # NEW: required field
    objectStoreCacheMemoryLimit: "1Gi"
    objectStoreCacheFileSize: "50Gi"
    verbose: true
    extraEnvVars: []
```

### Required Migration Steps

#### Step 1: Update your values.yaml structure

1. **Move serviceAccount annotations:**

   ```yaml
   # OLD
   brainstore:
     annotations:
       serviceaccount: {}

   # NEW
   brainstore:
     serviceAccount:
       annotations: {}
   ```

2. **Move image configuration to shared section:**

   ```yaml
   # OLD (under brainstore directly)
   brainstore:
     image:
       repository: public.ecr.aws/braintrust/brainstore
       tag: v1.1.21
       pullPolicy: Always

   # NEW (shared between reader/writer)
   brainstore:
     image:
       repository: public.ecr.aws/braintrust/brainstore
       tag: v1.1.21
       pullPolicy: IfNotPresent
   ```

3. **Split brainstore configuration into reader/writer sections:**
   - Copy your existing brainstore config to both `brainstore.reader` and `brainstore.writer`
   - Remove the old top-level brainstore fields (except shared ones)
   - Add `cacheSizeLimit: "50Gi"` to both reader and writer sections

#### Step 2: Update replica counts and resources (optional)

You can now scale readers and writers independently:

```yaml
brainstore:
  reader:
    replicas: 2  # Scale for read workload
    resources:
      requests:
        cpu: "4"    # Readers may need less CPU
        memory: "8Gi"
  writer:
    replicas: 1  # Fewer writers needed
    resources:
      requests:
        cpu: "8"    # Writers may need more CPU
        memory: "16Gi"
```

#### Step 3: Deploy the upgrade

```bash
helm upgrade braintrust ./braintrust -f your-values.yaml
```

### New Features

1. **Independent Scaling**: Scale readers and writers based on workload patterns
2. **Performance Optimization**: Dedicated read-only replicas for better read performance
3. **Resource Allocation**: Allocate different resources to readers vs writers
4. **Improved Caching**: Better cache management with separate reader/writer instances

### Rollback Instructions

If you need to rollback to v1.x:

1. **Restore old values.yaml structure** (see "Before" example above)
2. **Rollback Helm release:**

   ```bash
   helm rollback braintrust <previous-revision>
   ```

### Validation

After upgrading, verify the deployment:

```bash
# Check pods are running
kubectl get pods -n braintrust | grep brainstore

# Verify services
kubectl get svc -n braintrust | grep brainstore
```

You should see:

- `brainstore-reader-*` pods running
- `brainstore-writer-*` pods running
- `brainstore-reader` and `brainstore-writer` services

### Support

If you encounter issues during the upgrade, please:

1. Check the validation steps above
2. Review your values.yaml against the new structure
3. Reach out over slack to Support
