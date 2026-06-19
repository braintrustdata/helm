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
