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
