#!/bin/bash

# Test script for Braintrust Helm chart
# Runs both unit tests and integration tests

set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Change to the script's directory
cd "$SCRIPT_DIR" || exit 1

CHART_DIR="braintrust"

# Check if helm is installed
if ! command -v helm &> /dev/null; then
    echo "❌ Helm is not installed. Please install Helm first."
    exit 1
fi

# Check if helm-unittest is installed using helm plugin list
# Suppress stderr to avoid plugin loading errors from other plugins
HELM_UNITTEST_INSTALLED=false
if helm plugin list 2>/dev/null | grep -q "unittest"; then
    HELM_UNITTEST_INSTALLED=true
fi

# If not found, try to install it
if [ "$HELM_UNITTEST_INSTALLED" = false ]; then
    echo "⚠️  helm-unittest plugin not found. Installing..."
    # Suppress plugin loading errors by redirecting stderr
    helm plugin install https://github.com/helm-unittest/helm-unittest.git --verify=false 2>/dev/null || {
        echo "❌ Failed to install helm-unittest plugin"
        exit 1
    }
    HELM_UNITTEST_INSTALLED=true
fi

echo ""
echo "=========================================="
echo "1. Running Unit Tests (helm-unittest)"
echo "=========================================="

# Run helm unittest, suppressing plugin loading errors from stderr
if helm unittest "$CHART_DIR" 2>/dev/null; then
    echo ""
    echo "✅ Unit tests passed"
else
    echo ""
    echo "❌ Unit tests failed"
    exit 1
fi

echo ""
echo "=========================================="
echo "2. Testing Chart Rendering"
echo "=========================================="
echo ""

# Test rendering with different values files
VALUES_FILES=(
    "$CHART_DIR/ci/values-azure.yaml"
    "$CHART_DIR/ci/values-google.yaml"
    "$CHART_DIR/ci/values-aws.yaml"
    "$CHART_DIR/ci/values-minimal.yaml"
)

RENDER_FAILED=false

for values_file in "${VALUES_FILES[@]}"; do
    if [ -f "$values_file" ]; then
        echo "Testing with $(basename "$values_file")..."
        if helm template test-release "$CHART_DIR" --values "$values_file" > /dev/null 2>&1; then
            echo "  ✅ Rendered successfully"
        else
            echo "  ❌ Failed to render"
            RENDER_FAILED=true
        fi
    else
        echo "  ⚠️  Values file not found: $values_file"
    fi
done

if [ "$RENDER_FAILED" = true ]; then
    echo ""
    echo "❌ Chart rendering tests failed"
    exit 1
fi

echo ""
echo "✅ Chart rendering tests passed"

echo ""
echo "=========================================="
echo "3. Running Helm Lint"
echo "=========================================="
echo ""

if helm lint "$CHART_DIR" --strict; then
    echo ""
    echo "✅ Chart linting passed"
else
    echo ""
    echo "❌ Chart linting failed"
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ All tests passed!"
echo "=========================================="
