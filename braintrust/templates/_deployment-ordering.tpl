{{/* Defaults also cover pre-feature helm upgrade --reuse-values. */}}
{{- define "braintrust.deploymentOrdering.config" -}}
{{- $defaults := dict
  "enabled" true
  "minimumVersion" ""
  "migrationImage" ""
  "timeoutSeconds" 1200
  "pollIntervalSeconds" 15
  "backoffLimit" 2
  "image" "python:3.13-alpine@sha256:79e7a9b9ff1cbceff819f856fb374477792a5967759d94df266de7b7b4120e6f"
  "resources" (dict "requests" (dict "cpu" "25m" "memory" "32Mi") "limits" (dict "cpu" "100m" "memory" "128Mi"))
-}}
{{- $config := mergeOverwrite (deepCopy $defaults) (deepCopy (.Values.deploymentOrdering | default dict)) -}}
{{- if $config.enabled -}}
{{- $_ := required "deploymentOrdering.image is required" $config.image -}}
{{- range $field := list "timeoutSeconds" "pollIntervalSeconds" -}}
{{- if not (regexMatch "^[1-9][0-9]*$" (toString (index $config $field))) -}}
{{- fail "deploymentOrdering timing values must be positive integers" -}}
{{- end -}}
{{- end -}}
{{- if ge (int $config.pollIntervalSeconds) (int $config.timeoutSeconds) -}}
{{- fail "deploymentOrdering.timeoutSeconds must exceed pollIntervalSeconds" -}}
{{- end -}}
{{- if not (regexMatch "^(0|[1-9][0-9]*)$" (toString $config.backoffLimit)) -}}
{{- fail "deploymentOrdering.backoffLimit must be a nonnegative integer" -}}
{{- end -}}
{{- end -}}
{{- toYaml $config -}}
{{- end -}}

{{/* Preserve the revision suffix even for long release names. */}}
{{- define "braintrust.migrations.name" -}}
{{- printf "%s-%s-migrate-r%d" (.Release.Name | trunc 24 | trimSuffix "-") (.Release.Name | sha256sum | trunc 8) (int .Release.Revision) -}}
{{- end -}}

{{/* Migration scripts ship in standalone-api, never in the Brainstore or gate
helper image. Preserve API pins, but translate a newer Brainstore release to
the shared API repository without carrying over the Brainstore digest. */}}
{{- define "braintrust.migrations.image" -}}
{{- $config := include "braintrust.deploymentOrdering.config" . | fromYaml -}}
{{- if $config.enabled -}}
{{- $stableRelease := "^v?(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$" -}}
{{- $images := list -}}
{{- range $pool := include "braintrust.apiPools" . | fromYamlArray -}}
{{- $images = append $images (dict "repository" $pool.config.image.repository "tag" (toString $pool.config.image.tag) "brainstore" false) -}}
{{- end -}}
{{- $images = append $images (dict "repository" .Values.api.image.repository "tag" (toString .Values.brainstore.image.tag) "brainstore" true) -}}
{{- $selectedImage := "" -}}
{{- $selectedVersion := "" -}}
{{- $major := "" -}}
{{- $opaque := false -}}
{{- range $image := $images -}}
{{- $tag := first (splitList "@" $image.tag) -}}
{{- if regexMatch $stableRelease $tag -}}
{{- $version := trimPrefix "v" $tag -}}
{{- $imageMajor := first (splitList "." $version) -}}
{{- if and $major (ne $major $imageMajor) -}}
{{- fail "deploymentOrdering cannot migrate API and Brainstore service images from different major releases" -}}
{{- end -}}
{{- $major = $imageMajor -}}
{{- if or (not $selectedVersion) (semverCompare (printf ">%s" $selectedVersion) $version) -}}
{{- $selectedVersion = $version -}}
{{- $selectedTag := $image.tag -}}
{{- if $image.brainstore -}}
{{- $selectedTag = $tag -}}
{{- end -}}
{{- $selectedImage = printf "%s:%s" $image.repository $selectedTag -}}
{{- end -}}
{{- else -}}
{{- $opaque = true -}}
{{- end -}}
{{- end -}}
{{- $override := $config.migrationImage -}}
{{- if kindIs "invalid" $override -}}
{{- $override = "" -}}
{{- end -}}
{{- if not (kindIs "string" $override) -}}
{{- fail "deploymentOrdering.migrationImage must be a full standalone-API image reference" -}}
{{- end -}}
{{- if and $opaque (not $override) -}}
{{- fail "deploymentOrdering.migrationImage is required for opaque API or Brainstore service image tags; minimumVersion cannot select a migration bundle" -}}
{{- end -}}
{{- $minimum := $config.minimumVersion | default "" | toString -}}
{{- if and $opaque (not $minimum) -}}
{{- fail "deploymentOrdering.minimumVersion is required for opaque API or Brainstore service image tags" -}}
{{- end -}}
{{- if and $minimum (not (regexMatch $stableRelease $minimum)) -}}
{{- fail "deploymentOrdering.minimumVersion must be a stable release version" -}}
{{- end -}}
{{- if $override -}}
{{- $reference := first (splitList "@" $override) -}}
{{- $lastComponent := last (splitList "/" $reference) -}}
{{- $overrideTag := "" -}}
{{- if contains ":" $lastComponent -}}
{{- $overrideTag = last (splitList ":" $lastComponent) -}}
{{- end -}}
{{- if regexMatch $stableRelease $overrideTag -}}
{{- $requiredVersion := $selectedVersion -}}
{{- if and $minimum (or (not $requiredVersion) (semverCompare (printf ">%s" $requiredVersion) $minimum)) -}}
{{- $requiredVersion = trimPrefix "v" $minimum -}}
{{- end -}}
{{- if and $requiredVersion (semverCompare (printf "<%s" $requiredVersion) $overrideTag) -}}
{{- fail (printf "deploymentOrdering.migrationImage release %s is older than required release %s" $overrideTag $requiredVersion) -}}
{{- end -}}
{{- end -}}
{{- $override -}}
{{- else -}}
{{- $selectedImage -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "braintrust.migrations.initContainer" -}}
{{- $config := include "braintrust.deploymentOrdering.config" . | fromYaml -}}
- name: wait-for-migrations
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
      value: {{ include "braintrust.migrations.name" . | quote }}
    - name: JOB_TIMEOUT_SECONDS
      value: {{ $config.timeoutSeconds | quote }}
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

{{- define "braintrust.migrations.volumes" -}}
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
