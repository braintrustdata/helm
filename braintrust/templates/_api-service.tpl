{{/* Render one API Service from a merged pool configuration. */}}
{{- define "braintrust.apiService" -}}
{{- $root := .root -}}
{{- $api := .api -}}
{{- $role := .role -}}
{{- $poolLabels := dict -}}
{{- if or $root.Values.api.workloadIsolation.enabled (ne $role "default") -}}
{{- $_ := set $poolLabels "braintrust.com/api-pool" $role -}}
{{- end -}}
{{- $resourceLabels := mergeOverwrite (deepCopy $root.Values.global.labels) (deepCopy $api.labels) $poolLabels -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ $api.service.name | default $api.name }}
  namespace: {{ include "braintrust.namespace" $root }}
  {{- with $resourceLabels }}
  labels:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $api.annotations.service }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  selector:
    app: {{ $api.name }}
  ports:
    - name: {{ $api.service.portName }}
      protocol: TCP
      port: {{ $api.service.port }}
      targetPort: {{ $api.service.port }}
  type: {{ $api.service.type }}
{{- end -}}
