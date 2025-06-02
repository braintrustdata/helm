#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Change to the script's directory
cd "$SCRIPT_DIR" || exit 1

echo "Testing Helm chart template rendering..."
RELEASE_NAME="test-$(date +%s)"
TEMP_DIR=$(mktemp -d)
echo "Writing templates to TEMP_DIR: $TEMP_DIR"

# Render the templates to a file
helm template "$RELEASE_NAME" "$SCRIPT_DIR/braintrust" \
    --set global.namespace="$RELEASE_NAME" \
    > "$TEMP_DIR/rendered.yaml"

echo "Creating namespace..."
kubectl create namespace "$RELEASE_NAME" --save-config

# shellcheck disable=SC2317
cleanup() {
    if [ -n "$RELEASE_NAME" ]; then
        echo "Cleaning up namespace..."
        kubectl delete namespace "$RELEASE_NAME"
    fi
}
trap cleanup EXIT

echo "Validating rendered templates..."
kubectl apply --dry-run=server -f "$TEMP_DIR/rendered.yaml"

if kubectl apply --dry-run=server -f "$TEMP_DIR/rendered.yaml"; then
    echo "✅ Chart templates rendered successfully and are valid Kubernetes resources"
    exit 0
else
    echo "❌ Chart template validation failed"
    exit 1
fi
