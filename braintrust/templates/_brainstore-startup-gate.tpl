{{/* The floor belongs to the API, not the Brainstore image being requested.
An API-only image bump must not silently accept the still-old Brainstore fleet.
*/}}
{{- define "braintrust.brainstoreStartupGate.minimumVersion" -}}
{{- $minimum := .gate.minimumVersion | default (first (splitList "@" .api.image.tag)) -}}
{{- if not (regexMatch "^v?(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$" $minimum) -}}
{{- fail "deploymentOrdering requires a stable API release tag or an explicit stable minimumVersion" -}}
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
{{- if .Values.brainstore.automationwriter -}}
{{- $config := include "braintrust.automationWriter.config" . | fromYaml -}}
{{- $targets = append $targets (dict "deployment" $config.name "container" "brainstore-automationwriter") -}}
{{- end -}}
{{- toJson $targets -}}
{{- end -}}

{{- define "braintrust.brainstoreStartupGate.validateApi" -}}
{{- $rolling := .api.strategy.rollingUpdate | default dict -}}
{{- if or (ne .api.strategy.type "RollingUpdate") (not (has (toString $rolling.maxUnavailable) (list "0" "0%"))) -}}
{{- fail "deploymentOrdering requires RollingUpdate with maxUnavailable: 0 for every API pool" -}}
{{- end -}}
{{- if has (toString $rolling.maxSurge) (list "0" "0%" "<nil>") -}}
{{- fail "deploymentOrdering requires nonzero maxSurge for every API pool" -}}
{{- end -}}
{{- end -}}

{{- define "braintrust.brainstoreStartupGate.initContainer" -}}
{{- $config := include "braintrust.deploymentOrdering.config" .root | fromYaml -}}
- name: wait-for-brainstore
  image: {{ $config.image | quote }}
  imagePullPolicy: IfNotPresent
  command: ["python3", "-B", "-u", "/opt/migration-gate/brainstore_gate.py"]
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    runAsUser: 65532
    runAsGroup: 65532
    capabilities:
      drop: ["ALL"]
    seccompProfile:
      type: RuntimeDefault
  resources:
    {{- toYaml $config.resources | nindent 4 }}
  env:
    - name: POD_NAMESPACE
      valueFrom:
        fieldRef:
          fieldPath: metadata.namespace
    - name: BRAINSTORE_MINIMUM_VERSION
      value: {{ include "braintrust.brainstoreStartupGate.minimumVersion" (dict "gate" $config "api" .api) | quote }}
    - name: BRAINSTORE_DEPLOYMENTS
      value: {{ include "braintrust.brainstoreStartupGate.targets" .root | quote }}
    - name: BRAINSTORE_GATE_TIMEOUT_SECONDS
      value: {{ $config.timeoutSeconds | quote }}
    - name: BRAINSTORE_GATE_POLL_SECONDS
      value: {{ $config.pollIntervalSeconds | quote }}
    - name: BRAINSTORE_MINIMUM_REVISION
      value: {{ .root.Release.Revision | quote }}
  volumeMounts:
    - name: migration-gate-code
      mountPath: /opt/migration-gate
      readOnly: true
    - name: migration-gate-credentials
      mountPath: /var/run/brainstore-gate
      readOnly: true
{{- end -}}
