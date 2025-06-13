#!/bin/bash
# This is for internal braintrust testing only

set -euo pipefail
namespace="braintrust"

echo "Setting Kubernetes context..."
kubectl config use-context bt-azure-k8s-admin

echo "Creating namespace..."
kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f -

echo "Applying secrets..."
kubectl apply -n "${namespace}" -f secrets.yaml

echo "Deploying Helm chart..."
helm upgrade --install braintrust \
    ./braintrust \
    --namespace "${namespace}" \
    --values override-values.yaml \
    --wait \
    --timeout 5m

echo "Deployment completed successfully!"
echo
echo "To see the status of the deployment, run:"
echo "kubectl get pods -n ${namespace}"
echo
echo "To see the logs of the deployment, run:"
echo "kubectl logs -n ${namespace} -l app=braintrust-api"
echo "kubectl logs -n ${namespace} -l app=brainstore"
echo
echo "To port forward the API, run:"
echo "./port-forward.sh"

