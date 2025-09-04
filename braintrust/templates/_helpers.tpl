{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "apps.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "apps.labels" -}}
helm.sh/chart: {{ include "apps.chart" .}}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.labels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Service-specific labels that combine common labels with service labels
Usage: {{ include "apps.service-labels" (dict "context" . "service" .Values.api) }}
*/}}
{{- define "apps.service-labels" -}}
{{- $commonLabels := include "apps.labels" .context | fromYaml }}
{{- $serviceLabels := .service.labels | default dict }}
{{- $mergedLabels := merge $serviceLabels $commonLabels }}
{{- toYaml $mergedLabels }}
{{- end }}

{{/*
Service full name - used for resource naming
Usage: {{ include "apps.servicefullname" (dict "context" . "service" .Values.api) }}
*/}}
{{- define "apps.servicefullname" -}}
{{- .service.name }}
{{- end }}

{{/*
Service selector labels - used for pod selection in deployments and PDBs
Usage: {{ include "apps.serviceSelectorLabels" (dict "context" . "service" .Values.api) }}
*/}}
{{- define "apps.serviceSelectorLabels" -}}
app: {{ .service.name }}
{{- end }}
