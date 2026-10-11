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
port: 4000
service:
  name: ""
  type: ClusterIP
  port: 4000
  portName: http
resources:
  requests:
    cpu: "32"
    memory: "64Gi"
  limits:
    cpu: "32"
    memory: "64Gi"
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
