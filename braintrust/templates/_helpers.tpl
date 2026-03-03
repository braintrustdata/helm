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
