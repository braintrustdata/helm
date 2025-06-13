#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR" || exit 1

# Get the API pod name
API_POD=$(kubectl get pods -l app=braintrust-api -o name | head -n 1 | cut -d'/' -f2)

if [ -z "$API_POD" ]; then
    echo "❌ No API pod found. Are you using the right context and namespace?"
    echo "Current context: $(kubectl config current-context)"
    echo "Current namespace: $(kubectl config view --minify -o jsonpath='{.contexts[0].context.namespace}')"
    exit 1
fi

echo "Found API pod: $API_POD"
echo "Setting up port forwarding to http://localhost:8000"
echo "Press Ctrl+C to stop port forwarding"

# Set up port forwarding
kubectl port-forward "pod/$API_POD" 8000:8000
