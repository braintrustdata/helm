{{/* Render one API Deployment from a merged pool configuration. */}}
{{- define "braintrust.apiDeployment" -}}
{{- $root := .root -}}
{{- $api := .api -}}
{{- $role := .role -}}
{{- $customCA := $api.customCA -}}
{{- $customCAMountPath := "" -}}
{{- $customCAFilename := "" -}}
{{- $customCASecretName := "" -}}
{{- $customCASecretKey := "" -}}
{{- if $customCA.enabled -}}
{{- $customCAMountPath = required "api.customCA.mountPath is required when api.customCA.enabled is true" $customCA.mountPath -}}
{{- $customCAFilename = required "api.customCA.filename is required when api.customCA.enabled is true" $customCA.filename -}}
{{- $customCASecretName = required "api.customCA.secretName is required when api.customCA.enabled is true" $customCA.secretName -}}
{{- $customCASecretKey = required "api.customCA.secretKey is required when api.customCA.enabled is true" $customCA.secretKey -}}
{{- end -}}
{{- $poolLabels := dict -}}
{{- if or $root.Values.api.workloadIsolation.enabled (ne $role "default") -}}
{{- $_ := set $poolLabels "braintrust.dev/api-pool" $role -}}
{{- end -}}
{{- $resourceLabels := mergeOverwrite (deepCopy $root.Values.global.labels) (deepCopy $api.labels) $poolLabels -}}
{{- $podLabels := mergeOverwrite (deepCopy $root.Values.global.labels) (deepCopy $api.labels) (deepCopy $api.podLabels) (dict "app" $api.name) $poolLabels -}}
{{- if eq $root.Values.cloud "azure" -}}
{{- $_ := set $podLabels "azure.workload.identity/use" "true" -}}
{{- end -}}
{{- if and (eq $root.Values.cloud "google") $api.enableGcsAuth -}}
{{- $_ := set $podLabels "gke-workload-identity/use" "true" -}}
{{- end -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $api.name }}
  namespace: {{ include "braintrust.namespace" $root }}
  {{- with $resourceLabels }}
  labels:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $api.annotations.deployment }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  replicas: {{ $api.replicas }}
  strategy:
    type: {{ $api.strategy.type }}
    {{- with $api.strategy.rollingUpdate }}
    rollingUpdate:
      {{- toYaml . | nindent 6 }}
    {{- end }}
  selector:
    matchLabels:
      app: {{ $api.name }}
  template:
    metadata:
      labels:
        {{- with $podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      annotations:
        checksum/config: {{ include (print $root.Template.BasePath "/api-configmap.yaml") $root | sha256sum }}
        {{- if and (eq $root.Values.cloud "google") $api.enableGcsAuth }}
        iam.gke.io/gcp-service-account: {{ required "api.serviceAccount.googleServiceAccount is required when api.enableGcsAuth is true" $api.serviceAccount.googleServiceAccount }}
        {{- end }}
        {{- with $api.annotations.pod }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ $api.serviceAccount.name }}
      {{- with $api.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $api.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $api.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- if $api.topologySpread.enabled }}
      topologySpreadConstraints:
        - maxSkew: {{ $api.topologySpread.maxSkew }}
          topologyKey: {{ $api.topologySpread.topologyKey | quote }}
          whenUnsatisfiable: {{ $api.topologySpread.whenUnsatisfiable }}
          labelSelector:
            matchLabels:
              app: {{ $api.name }}
      {{- end }}
      {{- with $api.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: api
          image: "{{ $api.image.repository }}:{{ $api.image.tag }}"
          imagePullPolicy: {{ $api.image.pullPolicy }}
          {{- with $api.securityContext }}
          securityContext:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          ports:
            - containerPort: {{ $api.service.port }}
          resources:
            {{- toYaml $api.resources | nindent 12 }}
          {{- with $api.livenessProbe }}
          livenessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with $api.readinessProbe }}
          readinessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          envFrom:
            - configMapRef:
                name: {{ $root.Values.api.name }}
          env:
            - name: PG_URL
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: PG_URL
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: REDIS_URL
            - name: FUNCTION_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: FUNCTION_SECRET_KEY
            - name: BRAINSTORE_LICENSE_KEY
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: BRAINSTORE_LICENSE_KEY
            {{- if eq $root.Values.cloud "azure" }}
            - name: AZURE_STORAGE_CONNECTION_STRING
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: AZURE_STORAGE_CONNECTION_STRING
            {{- end }}
            {{- if and (eq $root.Values.cloud "google") (not $api.enableGcsAuth) }}
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: GCS_ACCESS_KEY_ID
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: braintrust-secrets
                  key: GCS_SECRET_ACCESS_KEY
            {{- end }}
            - name: TS_API_HEALTHSERVER_HOST
              value: {{ $api.healthServer.host | quote }}
            - name: TS_API_HEALTHSERVER_PORT
              value: {{ $api.healthServer.port | quote }}
            {{- if $customCA.enabled }}
            {{- $customCAPath := printf "%s/%s" $customCAMountPath $customCAFilename }}
            - name: NODE_EXTRA_CA_CERTS
              value: {{ $customCAPath | quote }}
            - name: REQUESTS_CA_BUNDLE
              value: {{ $customCAPath | quote }}
            - name: SSL_CERT_FILE
              value: {{ $customCAPath | quote }}
            - name: CURL_CA_BUNDLE
              value: {{ $customCAPath | quote }}
            - name: AWS_CA_BUNDLE
              value: {{ $customCAPath | quote }}
            - name: PIP_CERT
              value: {{ $customCAPath | quote }}
            {{- end }}
            {{- with $api.extraEnvVars }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- if or $api.tmpVolume.enabled (and (eq $root.Values.cloud "azure") $root.Values.azure.enableAzureKeyVaultDriver) $customCA.enabled }}
          volumeMounts:
            {{- if $api.tmpVolume.enabled }}
            - name: tmp-volume
              mountPath: /tmp
            {{- end }}
            {{- if and (eq $root.Values.cloud "azure") $root.Values.azure.enableAzureKeyVaultDriver }}
            - name: secrets-store-inline
              mountPath: "/mnt/secrets-store"
              readOnly: true
            {{- end }}
            {{- if $customCA.enabled }}
            - name: custom-ca-bundle
              mountPath: {{ $customCAMountPath | quote }}
              readOnly: true
            {{- end }}
          {{- end }}
      {{- with $api.extraContainers }}
      {{- toYaml . | nindent 8 }}
      {{- end }}
      volumes:
        {{- if or $api.tmpVolume.enabled (and (eq $root.Values.cloud "azure") $root.Values.azure.enableAzureKeyVaultDriver) $customCA.enabled $api.extraVolumes }}
        {{- if $api.tmpVolume.enabled }}
        - name: tmp-volume
          emptyDir:
            {{- if $api.tmpVolume.sizeLimit }}
            sizeLimit: {{ $api.tmpVolume.sizeLimit | quote }}
            {{- else }}
            {}
            {{- end }}
        {{- end }}
        {{- if and (eq $root.Values.cloud "azure") $root.Values.azure.enableAzureKeyVaultDriver }}
        - name: secrets-store-inline
          csi:
            driver: secrets-store.csi.k8s.io
            readOnly: true
            volumeAttributes:
              secretProviderClass: {{ $root.Values.azure.keyVaultName }}
        {{- end }}
        {{- if $customCA.enabled }}
        - name: custom-ca-bundle
          secret:
            secretName: {{ $customCASecretName | quote }}
            items:
              - key: {{ $customCASecretKey | quote }}
                path: {{ $customCAFilename | quote }}
        {{- end }}
        {{- with $api.extraVolumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
        {{- else }}
        []
        {{- end }}
{{- end -}}
