{{/*
Get the namespace to use for resources
*/}}
{{- define "braintrust.namespace" -}}
{{- if .Values.global.createNamespace -}}
{{- .Values.global.namespace -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/*
Build backward-compatible Deployment rollout settings for one workload.
Template-level defaults are required because `helm upgrade --reuse-values` from
a chart version that predates these settings does not merge in new chart values.
*/}}
{{- define "braintrust.deploymentRollout.config" -}}
{{- $defaultStrategy := dict
  "type" "RollingUpdate"
  "rollingUpdate" (dict "maxSurge" "100%" "maxUnavailable" 0)
-}}
{{- $strategy := mergeOverwrite (deepCopy $defaultStrategy) (deepCopy (.config.strategy | default dict)) -}}
{{- $rollout := dict
  "minReadySeconds" (int (.config.minReadySeconds | default 0))
  "progressDeadlineSeconds" (int (.config.progressDeadlineSeconds | default 600))
  "strategy" $strategy
-}}
{{- toYaml $rollout -}}
{{- end -}}

{{/*
Validate Deployment rollout timing for one workload.
*/}}
{{- define "braintrust.deploymentRollout.validate" -}}
{{- $path := required "deployment rollout configuration path is required" .path -}}
{{- $minReadySeconds := int .config.minReadySeconds -}}
{{- $progressDeadlineSeconds := int .config.progressDeadlineSeconds -}}
{{- if le $progressDeadlineSeconds $minReadySeconds -}}
{{- fail (printf "%s.progressDeadlineSeconds (%d) must be greater than %s.minReadySeconds (%d)" $path $progressDeadlineSeconds $path $minReadySeconds) -}}
{{- end -}}
{{- end -}}

{{/*
Static fast reader query sources used by API.
*/}}
{{- define "braintrust.fastReaderQuerySourcesCsv" -}}
{{- $sources := list
  "summaryPaginatedObjectViewer [realtime]"
  "summaryPaginatedObjectViewer"
  "a602c972-1843-4ee1-b6bc-d3c1075cd7e7"
  "traceQueryFn-id"
  "traceQueryFn-rootSpanId"
  "fullSpanQueryFn-root_span_id"
  "fullSpanQueryFn-id"
-}}
{{- join "," $sources -}}
{{- end -}}

{{/*
Internal cluster URL for the API service.
*/}}
{{- define "braintrust.apiInternalUrl" -}}
http://{{ .Values.api.service.name | default .Values.api.name }}.{{ include "braintrust.namespace" . }}:{{ .Values.api.service.port }}
{{- end -}}

{{/*
Build one API pool from the shared api values plus pool-specific overrides.
Pool extraEnvVars are appended so shared provider configuration is retained.
*/}}
{{- define "braintrust.apiPoolConfig" -}}
{{- $base := deepCopy .root.Values.api -}}
{{- $_ := unset $base "workloadIsolation" -}}
{{- $overrides := deepCopy (.overrides | default dict) -}}
{{- $baseExtraEnvVars := get $base "extraEnvVars" | default (list) -}}
{{- $poolExtraEnvVars := get $overrides "extraEnvVars" | default (list) -}}
{{- $_ := unset $overrides "extraEnvVars" -}}
{{- $pool := mergeOverwrite $base $overrides -}}
{{- $_ := set $pool "extraEnvVars" (concat $baseExtraEnvVars $poolExtraEnvVars) -}}
{{- toYaml $pool -}}
{{- end -}}

{{/*
Return the API pools rendered by the chart. The default pool always exists;
ingest and background are added only when workload isolation is enabled.
*/}}
{{- define "braintrust.apiPools" -}}
{{- $default := include "braintrust.apiPoolConfig" (dict "root" . "overrides" (dict)) | fromYaml -}}
{{- $pools := list (dict "role" "default" "config" $default) -}}
{{- if .Values.api.workloadIsolation.enabled -}}
{{- $ingest := include "braintrust.apiPoolConfig" (dict "root" . "overrides" .Values.api.workloadIsolation.ingest) | fromYaml -}}
{{- $background := include "braintrust.apiPoolConfig" (dict "root" . "overrides" .Values.api.workloadIsolation.background) | fromYaml -}}
{{- $pools = append $pools (dict "role" "ingest" "config" $ingest) -}}
{{- $pools = append $pools (dict "role" "background" "config" $background) -}}
{{- end -}}
{{- toYaml $pools -}}
{{- end -}}

{{/*
Render the product-owned workload-isolation routes for an Istio VirtualService.
The static route contract is packaged with the chart at
files/contracts/api-workload-isolation-routes.yaml. User-supplied virtualService.http
routes are rendered after these routes as custom fallback behavior.
*/}}
{{- define "braintrust.apiWorkloadIsolationVirtualServiceRoutes" -}}
{{- $contract := .Files.Get "files/contracts/api-workload-isolation-routes.yaml" | fromYaml -}}
{{- $ingest := include "braintrust.apiPoolConfig" (dict "root" . "overrides" .Values.api.workloadIsolation.ingest) | fromYaml -}}
{{- $background := include "braintrust.apiPoolConfig" (dict "root" . "overrides" .Values.api.workloadIsolation.background) | fromYaml -}}
{{- $ingestDestination := dict "host" ($ingest.service.name | default $ingest.name) "port" (dict "number" $ingest.service.port) -}}
{{- $backgroundDestination := dict "host" ($background.service.name | default $background.name) "port" (dict "number" $background.service.port) -}}
{{- $routes := list -}}
{{- range $route := $contract.pools.ingest.routes -}}
{{- $match := dict "uri" (dict $route.pathType $route.path) -}}
{{- if $route.method -}}
{{- $_ := set $match "method" (dict "exact" $route.method) -}}
{{- end -}}
{{- $routes = append $routes (dict "match" (list $match) "route" (list (dict "destination" $ingestDestination))) -}}
{{- end -}}
{{- range $route := $contract.pools.background.routes -}}
{{- $match := dict "uri" (dict $route.pathType $route.path) -}}
{{- if $route.method -}}
{{- $_ := set $match "method" (dict "exact" $route.method) -}}
{{- end -}}
{{- $routes = append $routes (dict "match" (list $match) "route" (list (dict "destination" $backgroundDestination))) -}}
{{- end -}}
{{- toYaml $routes -}}
{{- end -}}

{{/*
Internal cluster URL Brainstore uses for function/scoring traffic. The
background pool is used only after workload isolation has been activated for
Brainstore, allowing existing deployments to stage a ready background pool.
*/}}
{{- define "braintrust.apiAiProxyInternalUrl" -}}
{{- if and .Values.api.workloadIsolation.enabled .Values.api.workloadIsolation.brainstoreAiProxyToBackground -}}
{{- $background := include "braintrust.apiPoolConfig" (dict "root" . "overrides" .Values.api.workloadIsolation.background) | fromYaml -}}
http://{{ $background.service.name | default $background.name }}:{{ $background.service.port }}
{{- else -}}
http://{{ .Values.api.service.name | default .Values.api.name }}:{{ .Values.api.service.port }}
{{- end -}}
{{- end -}}

{{/*
Internal cluster URL for the AI Gateway service.
*/}}
{{- define "braintrust.aiGatewayInternalUrl" -}}
http://{{ .Values.aiGateway.service.name | default .Values.aiGateway.name }}.{{ include "braintrust.namespace" . }}:{{ .Values.aiGateway.service.port }}
{{- end -}}

{{/*
Whether Loop Runtime is enabled. Values reused from a chart release that
predates Loop Runtime have no loopRuntime map, which means disabled.
*/}}
{{- define "braintrust.loopRuntime.enabled" -}}
{{- if (.Values.loopRuntime | default dict).enabled -}}true{{- end -}}
{{- end -}}

{{/*
Object-store scheme and Brainstore bucket for Loop Runtime. Matches the
Brainstore ConfigMaps so both read the same index, WAL, and locks.
*/}}
{{- define "braintrust.loopRuntime.brainstoreBucketUri" -}}
{{- if eq .Values.cloud "google" -}}
gs://{{ .Values.objectStorage.google.brainstoreBucket }}
{{- else -}}
s3://{{ .Values.objectStorage.aws.brainstoreBucket }}
{{- end -}}
{{- end -}}

{{- define "braintrust.loopRuntime.codeBundleUri" -}}
{{- if eq .Values.cloud "google" -}}
gs://{{ .Values.objectStorage.google.apiBucket }}
{{- else -}}
s3://{{ .Values.objectStorage.aws.codeBundleBucket }}
{{- end -}}
{{- end -}}

{{/*
Validate the Loop Runtime configuration. Terraform supplies the identity and
Sandbox Service infrastructure; Helm supplies the Kubernetes workload.
*/}}
{{- define "braintrust.loopRuntime.validate" -}}
{{- if include "braintrust.loopRuntime.enabled" . -}}
{{- range (list "image" "service" "serviceAccount" "sandbox") -}}
{{- if not (hasKey $.Values.loopRuntime .) -}}
{{- fail (printf "loopRuntime.%s is missing. Values reused from a chart release that predates Loop Runtime omit the loopRuntime defaults; upgrade with --reset-then-reuse-values or supply the full loopRuntime map." .) -}}
{{- end -}}
{{- end -}}
{{- $sandbox := .Values.loopRuntime.sandbox -}}
{{- if not (has .Values.cloud (list "aws" "google")) -}}
{{- fail "loopRuntime.enabled requires cloud: aws or cloud: google." -}}
{{- end -}}
{{- if eq .Values.cloud "aws" -}}
{{- $_ := required "objectStorage.aws.brainstoreBucket is required when loopRuntime.enabled is true" .Values.objectStorage.aws.brainstoreBucket -}}
{{- $_ := required "objectStorage.aws.codeBundleBucket is required when loopRuntime.enabled is true" .Values.objectStorage.aws.codeBundleBucket -}}
{{- else -}}
{{- $_ := required "objectStorage.google.brainstoreBucket is required when loopRuntime.enabled is true" .Values.objectStorage.google.brainstoreBucket -}}
{{- $_ := required "objectStorage.google.apiBucket is required when loopRuntime.enabled is true" .Values.objectStorage.google.apiBucket -}}
{{- $_ := required "loopRuntime.serviceAccount.googleServiceAccount is required when cloud is google (use Terraform output loop_runtime_service_account)" .Values.loopRuntime.serviceAccount.googleServiceAccount -}}
{{- end -}}
{{- $_ := required "loopRuntime.serviceAccount.name is required when loopRuntime.enabled is true" .Values.loopRuntime.serviceAccount.name -}}
{{- $urls := $sandbox.urls | default list -}}
{{- if not (kindIs "slice" $urls) -}}
{{- fail "loopRuntime.sandbox.urls must be a list of URLs" -}}
{{- end -}}
{{- if and $sandbox.dnsUrl $urls -}}
{{- fail "Set only one of loopRuntime.sandbox.dnsUrl and loopRuntime.sandbox.urls." -}}
{{- end -}}
{{- if not (or $sandbox.dnsUrl $urls) -}}
{{- fail "loopRuntime.sandbox.dnsUrl or loopRuntime.sandbox.urls is required when loopRuntime.enabled is true (GKE: use Terraform output gke_isolated_workers.discovery.endpoint)" -}}
{{- end -}}
{{- $_ := required "loopRuntime.sandbox.image is required when loopRuntime.enabled is true" $sandbox.image -}}
{{- if not (has $sandbox.egressMode (list "restricted" "internet")) -}}
{{- fail (printf "loopRuntime.sandbox.egressMode must be restricted or internet, got %q" (toString $sandbox.egressMode)) -}}
{{- end -}}
{{- range (prepend $urls ($sandbox.dnsUrl | default "")) -}}
{{- if and . (hasPrefix "https://" .) (not $sandbox.tls.secretName) -}}
{{- fail "loopRuntime.sandbox.tls.secretName is required for https:// Sandbox Service endpoints" -}}
{{- end -}}
{{- end -}}
{{- if $sandbox.tls.secretName -}}
{{- $_ := required "loopRuntime.sandbox.tls.certKey is required when tls.secretName is set" $sandbox.tls.certKey -}}
{{- $_ := required "loopRuntime.sandbox.tls.keyKey is required when tls.secretName is set" $sandbox.tls.keyKey -}}
{{- $_ := required "loopRuntime.sandbox.tls.caKey is required when tls.secretName is set" $sandbox.tls.caKey -}}
{{- end -}}
{{- if $sandbox.egressGateway.enabled -}}
{{- $_ := required "loopRuntime.sandbox.egressGateway.url is required when loopRuntime.sandbox.egressGateway.enabled is true" $sandbox.egressGateway.url -}}
{{- if eq (int $sandbox.egressGateway.port) (int .Values.loopRuntime.service.port) -}}
{{- fail "loopRuntime.sandbox.egressGateway.port must differ from loopRuntime.service.port" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Internal cluster URL for the Loop Runtime service. */}}
{{- define "braintrust.loopRuntimeInternalUrl" -}}
http://{{ .Values.loopRuntime.service.name | default .Values.loopRuntime.name }}.{{ include "braintrust.namespace" . }}:{{ .Values.loopRuntime.service.port }}
{{- end -}}

{{/* Service that exposes the Loop Runtime sandbox egress gateway. */}}
{{- define "braintrust.loopRuntimeEgressServiceName" -}}
{{- printf "%s-egress" (.Values.loopRuntime.service.name | default .Values.loopRuntime.name) -}}
{{- end -}}

{{/* Stable Redis key namespace for a Helm-managed Loop Runtime. */}}
{{- define "braintrust.loopRuntimeRedisNamespace" -}}
{{- .Values.loopRuntime.redisNamespace | default (printf "loop-runtime:%s" .Release.Name) -}}
{{- end -}}

{{/* Internal cluster URL for the Brainstore fast reader used by Loop Runtime. */}}
{{- define "braintrust.brainstoreFastReaderInternalUrl" -}}
http://{{ .Values.brainstore.fastreader.service.name | default .Values.brainstore.fastreader.name }}.{{ include "braintrust.namespace" . }}:{{ .Values.brainstore.fastreader.service.port }}
{{- end -}}

{{/* Product-owned Loop Runtime routes for an Istio VirtualService. */}}
{{- define "braintrust.loopRuntimeVirtualServiceRoutes" -}}
- match:
    - uri:
        exact: /loop/runtime
    - uri:
        prefix: /loop/runtime/
  route:
    - destination:
        host: {{ .Values.loopRuntime.service.name | default .Values.loopRuntime.name }}
        port:
          number: {{ .Values.loopRuntime.service.port }}
{{- end -}}

{{/*
Validate API autoscaling prerequisites (GKE + AutoscalingMetric CRD).
*/}}
{{- define "braintrust.apiAutoscaling.validate" -}}
{{- if ne .Values.cloud "google" }}
{{- fail "api.autoscaling is currently only supported when cloud is google (GKE)" }}
{{- end }}
{{- if not (.Capabilities.APIVersions.Has "autoscaling.gke.io/v1beta1") }}
{{- fail "api.autoscaling requires the AutoscalingMetric API (autoscaling.gke.io/v1beta1). Use GKE 1.35.1 or later, or verify with: kubectl api-resources | grep autoscalingmetric. For helm template without a cluster, pass --api-versions=autoscaling.gke.io/v1beta1." }}
{{- end }}
{{- end -}}

{{/*
Render Brainstore container resources with provider-specific ephemeral storage.

Google Autopilot keeps the legacy behavior of defaulting the ephemeral-storage
request to volume.size when no explicit total request is set. AWS EKS requires
an explicit total pod-local storage budget that includes cache, optional /tmp,
and normal writable-layer/log overhead.
*/}}
{{- define "braintrust.brainstoreResources" -}}
{{- $root := .root -}}
{{- $resources := deepCopy .resources -}}
{{- $supportsEphemeralStorage := or (eq $root.Values.cloud "aws") (and (eq $root.Values.cloud "google") (eq $root.Values.google.mode "autopilot")) -}}
{{- $request := "" -}}
{{- if and .ephemeralStorage .ephemeralStorage.request -}}
{{- $request = .ephemeralStorage.request -}}
{{- else if and (eq $root.Values.cloud "google") (eq $root.Values.google.mode "autopilot") .volumeSize -}}
{{- $request = .volumeSize -}}
{{- end -}}
{{- if and $supportsEphemeralStorage $request -}}
{{- $requests := deepCopy (default (dict) $resources.requests) -}}
{{- $_ := set $requests "ephemeral-storage" $request -}}
{{- $_ := set $resources "requests" $requests -}}
{{- end -}}
{{- if and $supportsEphemeralStorage .ephemeralStorage .ephemeralStorage.limit -}}
{{- $limits := deepCopy (default (dict) $resources.limits) -}}
{{- $_ := set $limits "ephemeral-storage" .ephemeralStorage.limit -}}
{{- $_ := set $resources "limits" $limits -}}
{{- end -}}
{{- toYaml $resources -}}
{{- end -}}

{{/*
Defaults for the optional automation writer pool.

helm upgrade --reuse-values does not merge new chart defaults. Enabling the
pool with only --set brainstore.automationwriter.replicas=1 leaves every other
field unset. Keep this map aligned with brainstore.automationwriter in values.yaml.
*/}}
{{- define "braintrust.automationWriter.defaults" -}}
name: brainstore-automationwriter
labels: {}
podLabels: {}
annotations:
  configmap: {}
  deployment: {}
  service: {}
  pod: {}
replicas: 0
minReadySeconds: 0
progressDeadlineSeconds: 600
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: "100%"
    maxUnavailable: 0
service:
  name: ""
  type: ClusterIP
  port: 4000
  portName: http
resources:
  requests:
    cpu: "32"
    memory: "128Gi"
  limits:
    cpu: "32"
    memory: "128Gi"
cacheDir: /mnt/tmp/brainstore
objectStoreCacheMemoryLimit: 1Gi
objectStoreCacheFileSize: 1000Gi
verbose: true
volume:
  size: ""
  sizeLimit: ""
ephemeralStorage:
  request: ""
  limit: ""
tmpVolume:
  enabled: false
  sizeLimit: ""
extraEnvVars: []
nodeSelector: {}
tolerations: []
affinity: {}
extraContainers: []
extraVolumes: []
{{- end -}}

{{/*
Replica count for the automation writer pool. Null and missing both mean 0.
An empty replicas field would make Kubernetes run 1 pod.
*/}}
{{- define "braintrust.automationWriter.replicas" -}}
{{- int ((dig "automationwriter" "replicas" 0 .Values.brainstore) | default 0) -}}
{{- end -}}

{{/*
Resolved automation writer config: chart defaults overlaid with user values.
A null replica count is 0. An empty replicas field would make Kubernetes run 1 pod.
*/}}
{{- define "braintrust.automationWriter.config" -}}
{{- include "braintrust.automationWriter.validate" . -}}
{{- $defaults := include "braintrust.automationWriter.defaults" . | fromYaml -}}
{{- $provided := deepCopy (.Values.brainstore.automationwriter | default dict) -}}
{{- $pool := mergeOverwrite (deepCopy $defaults) $provided -}}
{{- $_ := set $pool "replicas" (int (include "braintrust.automationWriter.replicas" .)) -}}
{{- toYaml $pool -}}
{{- end -}}

{{/*
BRAINSTORE_WRITER_LOOP_CONFIG is ignored before brainstore v2.16.0. Enabling
the pool on an older image adds writers that still run every writer loop.
Only stable release tags (vX.Y.Z) are checked. Commit SHAs and other custom
tags carry no version, so the operator is responsible for compatibility.
*/}}
{{- define "braintrust.automationWriter.validate" -}}
{{- $replicas := int (include "braintrust.automationWriter.replicas" .) -}}
{{- if gt $replicas 0 -}}
{{- $tag := .Values.brainstore.image.tag | toString -}}
{{- if regexMatch "^v?(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$" $tag -}}
{{- if not (semverCompare ">=2.16.0" $tag) -}}
{{- fail (printf "brainstore.automationwriter requires brainstore image v2.16.0 or newer. brainstore.image.tag is %q, which does not implement BRAINSTORE_WRITER_LOOP_CONFIG." $tag) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
