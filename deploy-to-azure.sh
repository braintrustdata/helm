#!/bin/bash
# This is for internal braintrust testing only

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

namespace="braintrust"

echo "Current Kubernetes context:"
kubectl config current-context

echo "Cluster URL:"
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'
echo

echo
read -p "Do you want to proceed with deployment to this kubernetes cluster? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    echo "Deployment cancelled."
    exit 1
fi

echo "Creating namespace..."
kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f -

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

