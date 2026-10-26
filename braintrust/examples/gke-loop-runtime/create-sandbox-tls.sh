#!/bin/bash
# Creates the mutual TLS Secrets shared by Loop Runtime and Sandbox Service.
# Run once before installing either release. Private keys stay in Kubernetes
# Secrets and never enter Terraform state. The certificates match those the
# AWS Terraform module creates: separate server and client CAs, P-256 keys,
# ten-year validity.
#
# Usage: create-sandbox-tls.sh <worker hostname> <worker kube context> <runtime kube context>
#   worker hostname: Terraform output gke_isolated_workers.discovery.hostname
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <worker hostname> <worker kube context> <runtime kube context>" >&2
  exit 2
fi
HOSTNAME_SAN="$1"
WORKER_CONTEXT="$2"
RUNTIME_CONTEXT="$3"
WORKER_NAMESPACE="${WORKER_NAMESPACE:-braintrust-sandbox}"
RUNTIME_NAMESPACE="${RUNTIME_NAMESPACE:-braintrust}"
DAYS=3650

# Fail before writing anything if either cluster is unreachable.
for context in "$WORKER_CONTEXT" "$RUNTIME_CONTEXT"; do
  kubectl --context "$context" get namespaces >/dev/null
done
# sandbox-tls is written last, so it marks a completed run. A partial run
# leaves only client Secrets, which a rerun replaces.
if kubectl --context "$WORKER_CONTEXT" -n "$WORKER_NAMESPACE" get secret sandbox-tls >/dev/null 2>&1; then
  echo "sandbox-tls already exists in $WORKER_CONTEXT/$WORKER_NAMESPACE; refusing to replace it." >&2
  exit 1
fi

dir="$(mktemp -d)"
trap 'rm -rf "$dir"' EXIT
cd "$dir"

for purpose in server client; do
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$purpose-ca.key" 2>/dev/null
  openssl req -x509 -new -sha256 -key "$purpose-ca.key" -days "$DAYS" \
    -subj "/CN=sandbox-$purpose-ca" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$purpose-ca.pem"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$purpose.key" 2>/dev/null
  {
    echo "basicConstraints=critical,CA:FALSE"
    echo "keyUsage=critical,digitalSignature"
    echo "authorityKeyIdentifier=keyid"
    if [ "$purpose" = server ]; then
      echo "extendedKeyUsage=serverAuth"
      echo "subjectAltName=DNS:$HOSTNAME_SAN"
    else
      echo "extendedKeyUsage=clientAuth"
    fi
  } >"$purpose.ext"
  openssl req -new -key "$purpose.key" -subj "/CN=sandbox-$purpose" |
    openssl x509 -req -sha256 -days "$DAYS" -CA "$purpose-ca.pem" -CAkey "$purpose-ca.key" \
      -set_serial "0x$(openssl rand -hex 16)" -extfile "$purpose.ext" -out "$purpose.pem" 2>/dev/null
done

apply_secret() {
  kubectl --context "$1" create namespace "$2" --dry-run=client -o yaml | kubectl --context "$1" apply -f - >/dev/null
  kubectl --context "$1" -n "$2" create secret generic "$3" "${@:4}" --dry-run=client -o yaml |
    kubectl --context "$1" apply -f -
}

# The worker controller and Loop Runtime present the client certificate and trust the server CA.
for target in "$RUNTIME_CONTEXT $RUNTIME_NAMESPACE" "$WORKER_CONTEXT $WORKER_NAMESPACE"; do
  # shellcheck disable=SC2086
  apply_secret $target sandbox-client-tls \
    --from-file=client.pem=client.pem --from-file=client.key=client.key --from-file=server-ca.pem=server-ca.pem
done
# Workers present the server certificate and trust the client CA.
apply_secret "$WORKER_CONTEXT" "$WORKER_NAMESPACE" sandbox-tls \
  --from-file=cert.pem=server.pem --from-file=key.pem=server.key --from-file=ca_cert.pem=client-ca.pem
