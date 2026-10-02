{{/* Shared configuration. Keep defaults for helm upgrade --reuse-values. */}}
{{- define "braintrust.brainstoreStartupGate.config" -}}
{{- $defaults := dict
  "enabled" true
  "minimumVersion" ""
  "image" "python:3.13-alpine@sha256:79e7a9b9ff1cbceff819f856fb374477792a5967759d94df266de7b7b4120e6f"
  "timeoutSeconds" 1200
  "pollIntervalSeconds" 15
  "resources" (dict "requests" (dict "cpu" "25m" "memory" "32Mi") "limits" (dict "cpu" "100m" "memory" "128Mi"))
-}}
{{- $gate := mergeOverwrite (deepCopy $defaults) (deepCopy (.Values.api.brainstoreStartupGate | default dict)) -}}
{{- if $gate.enabled -}}
{{- $_ := required "api.brainstoreStartupGate.image is required" $gate.image -}}
{{- range $field := list "timeoutSeconds" "pollIntervalSeconds" -}}
{{- if not (regexMatch "^[1-9][0-9]*$" (toString (index $gate $field))) -}}
{{- fail "api.brainstoreStartupGate timing values must be positive integers" -}}
{{- end -}}
{{- end -}}
{{- if ge (int $gate.pollIntervalSeconds) (int $gate.timeoutSeconds) -}}
{{- fail "api.brainstoreStartupGate timeoutSeconds must exceed pollIntervalSeconds for two observations" -}}
{{- end -}}
{{- end -}}
{{- toYaml $gate -}}
{{- end -}}

{{/* The floor belongs to the API, not the Brainstore image being requested.
An API-only image bump must not silently accept the still-old Brainstore fleet.
*/}}
{{- define "braintrust.brainstoreStartupGate.minimumVersion" -}}
{{- $minimum := .gate.minimumVersion | default (first (splitList "@" .api.image.tag)) -}}
{{- if not (regexMatch "^v?(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$" $minimum) -}}
{{- fail "api.brainstoreStartupGate requires a stable API release tag or an explicit stable minimumVersion" -}}
{{- end -}}
{{- trimPrefix "v" $minimum -}}
{{- end -}}

{{- define "braintrust.brainstoreStartupGate.name" -}}
{{- printf "%s-brainstore-startup" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "braintrust.brainstoreStartupGate.targets" -}}
{{- $targets := list -}}
{{- range $role := list "reader" "writer" "fastreader" -}}
{{- $config := index $.Values.brainstore $role -}}
{{- $targets = append $targets (dict "deployment" $config.name "container" (printf "brainstore-%s" $role)) -}}
{{- end -}}
{{- toJson $targets -}}
{{- end -}}

{{- define "braintrust.brainstoreStartupGate.validateApi" -}}
{{- $rolling := .api.strategy.rollingUpdate | default dict -}}
{{- if or (ne .api.strategy.type "RollingUpdate") (not (has (toString $rolling.maxUnavailable) (list "0" "0%"))) -}}
{{- fail "api.brainstoreStartupGate requires RollingUpdate with maxUnavailable: 0 for every API pool" -}}
{{- end -}}
{{- if has (toString $rolling.maxSurge) (list "0" "0%" "<nil>") -}}
{{- fail "api.brainstoreStartupGate requires nonzero maxSurge for every API pool" -}}
{{- end -}}
{{- end -}}
