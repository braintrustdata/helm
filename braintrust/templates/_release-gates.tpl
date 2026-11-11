{{/* Defaults also cover pre-feature helm upgrade --reuse-values. */}}
{{- define "braintrust.migrations.config" -}}
{{- $defaults := dict
  "enabled" true
  "timeoutSeconds" 1200
  "pollIntervalSeconds" 5
  "backoffLimit" 2
  "image" "python:3.13-alpine@sha256:79e7a9b9ff1cbceff819f856fb374477792a5967759d94df266de7b7b4120e6f"
  "resources" (dict "requests" (dict "cpu" "25m" "memory" "32Mi") "limits" (dict "cpu" "100m" "memory" "128Mi"))
-}}
{{- $config := mergeOverwrite (deepCopy $defaults) (deepCopy (.Values.migrations | default dict)) -}}
{{- if $config.enabled -}}
{{- $_ := required "migrations.image is required" $config.image -}}
{{- range $field := list "timeoutSeconds" "pollIntervalSeconds" -}}
{{- if not (regexMatch "^[1-9][0-9]*$" (toString (index $config $field))) -}}
{{- fail "migrations timing values must be positive integers" -}}
{{- end -}}
{{- end -}}
{{- if ge (int $config.pollIntervalSeconds) (int $config.timeoutSeconds) -}}
{{- fail "migrations.timeoutSeconds must exceed pollIntervalSeconds" -}}
{{- end -}}
{{- if not (regexMatch "^(0|[1-9][0-9]*)$" (toString $config.backoffLimit)) -}}
{{- fail "migrations.backoffLimit must be a nonnegative integer" -}}
{{- end -}}
{{- end -}}
{{- toYaml $config -}}
{{- end -}}

{{/* Preserve the revision suffix even for long release names. */}}
{{- define "braintrust.migrations.name" -}}
{{- printf "%s-%s-migrate-r%d" (.Release.Name | trunc 24 | trimSuffix "-") (.Release.Name | sha256sum | trunc 8) (int .Release.Revision) -}}
{{- end -}}

{{- define "braintrust.migrations.gatewayAccount" -}}
{{- printf "%s-migration-gate" (.Release.Name | trunc 48 | trimSuffix "-") -}}
{{- end -}}

{{/* Two completed Jobs preserve release ordering without pinning old API
replacements to a Brainstore rollout that has since been superseded. */}}
{{- define "braintrust.rollout.enabled" -}}
{{- $migration := include "braintrust.migrations.config" . | fromYaml -}}
{{- $gate := include "braintrust.brainstoreStartupGate.config" . | fromYaml -}}
{{- if and $migration.enabled $gate.enabled -}}true{{- end -}}
{{- end -}}

{{- define "braintrust.rollout.name" -}}
{{- printf "%s-%s-rollout-r%d" (.Release.Name | trunc 24 | trimSuffix "-") (.Release.Name | sha256sum | trunc 8) (int .Release.Revision) -}}
{{- end -}}

{{- define "braintrust.rollout.serviceAccount" -}}
{{- printf "%s-%s-rollout" (.Release.Name | trunc 24 | trimSuffix "-") (.Release.Name | sha256sum | trunc 8) -}}
{{- end -}}

{{/* Reserve rollout identity/checksum only while the exact release gate is on.
Merge annotations before rendering so user input cannot create duplicate keys. */}}
{{- define "braintrust.rollout.podAnnotations" -}}
{{- $root := .root -}}
{{- $checksum := include (printf "%s/brainstore-%s-configmap.yaml" $root.Template.BasePath .role) $root | sha256sum -}}
{{- $annotations := dict "checksum/config" $checksum -}}
{{- if eq $root.Values.cloud "google" -}}
{{- $_ := set $annotations "iam.gke.io/gcp-service-account" (required "brainstore.serviceAccount.googleServiceAccount is required when cloud is google" $root.Values.brainstore.serviceAccount.googleServiceAccount) -}}
{{- end -}}
{{- $annotations = mergeOverwrite $annotations (deepCopy (.config.annotations.pod | default dict)) -}}
{{- if include "braintrust.rollout.enabled" $root -}}
{{- $_ := set $annotations "checksum/config" $checksum -}}
{{- $_ := set $annotations "braintrust.dev/release-revision" (toString $root.Release.Revision) -}}
{{- end -}}
{{- toYaml $annotations -}}
{{- end -}}


{{/* The revision binds image, inline env and all other Pod template changes;
the existing checksum binds ConfigMap-backed environment without copying secrets. */}}
{{- define "braintrust.rollout.targets" -}}
{{- $targets := list -}}
{{- $image := printf "%s:%s" .Values.brainstore.image.repository .Values.brainstore.image.tag -}}
{{- range $role := list "reader" "writer" "fastreader" -}}
{{- $config := index $.Values.brainstore $role -}}
{{- $checksum := include (printf "%s/brainstore-%s-configmap.yaml" $.Template.BasePath $role) $ | sha256sum -}}
{{- $targets = append $targets (dict "deployment" $config.name "container" (printf "brainstore-%s" $role) "revision" (toString $.Release.Revision) "image" $image "configChecksum" $checksum) -}}
{{- end -}}
{{- if .Values.brainstore.automationwriter -}}
{{- $config := include "braintrust.automationWriter.config" . | fromYaml -}}
{{- $checksum := include (print .Template.BasePath "/brainstore-automationwriter-configmap.yaml") . | sha256sum -}}
{{- $targets = append $targets (dict "deployment" $config.name "container" "brainstore-automationwriter" "revision" (toString .Release.Revision) "image" $image "configChecksum" $checksum) -}}
{{- end -}}
{{- toJson $targets -}}
{{- end -}}

{{/* One read-only Job gate serves both release phases. The rollout gate includes
the migration budget because its Job first waits for migrations itself. */}}
{{- define "braintrust.releaseJob.initContainer" -}}
{{- $root := .root -}}
{{- $config := include "braintrust.migrations.config" $root | fromYaml -}}
{{- $job := include "braintrust.migrations.name" $root -}}
{{- $name := "wait-for-migrations" -}}
{{- $timeout := int $config.timeoutSeconds -}}
{{- if eq (.phase | default "migration") "rollout" -}}
{{- $gate := include "braintrust.brainstoreStartupGate.config" $root | fromYaml -}}
{{- $timeout = int (add $timeout (int $gate.timeoutSeconds)) -}}
{{- $config = $gate -}}
{{- $job = include "braintrust.rollout.name" $root -}}
{{- $name = "wait-for-brainstore-rollout" -}}
{{- end -}}
- name: {{ $name }}
  image: {{ $config.image | quote }}
  imagePullPolicy: IfNotPresent
  command: ["python3", "-B", "-u", "/opt/migration-gate/gate.py"]
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
    - name: JOB_NAME
      value: {{ $job | quote }}
    - name: JOB_TIMEOUT_SECONDS
      value: {{ $timeout | quote }}
    - name: JOB_POLL_SECONDS
      value: {{ $config.pollIntervalSeconds | quote }}
  volumeMounts:
    - name: migration-gate-code
      mountPath: /opt/migration-gate
      readOnly: true
    - name: migration-gate-credentials
      mountPath: /var/run/migration-gate
      readOnly: true
{{- end -}}

{{- define "braintrust.releaseJob.volumes" -}}
- name: migration-gate-code
  configMap:
    name: {{ include "braintrust.migrations.name" . }}-gate
- name: migration-gate-credentials
  projected:
    sources:
      - serviceAccountToken:
          path: token
          expirationSeconds: 3600
      - configMap:
          name: kube-root-ca.crt
          items:
            - key: ca.crt
              path: ca.crt
{{- end -}}
