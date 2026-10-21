#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

# Content-addressed worker chart distributed with Braintrust v2.18.0.
CHART_SHA256=cd27a3983e304932a7e1617adf3105c2b7320a340f26bfea577df781827142f9
CHART_URL="https://braintrust-cf-us-east-1.s3.amazonaws.com/sandbox-service/$CHART_SHA256/sandbox-service-0.1.0.tgz"
CHART_ARCHIVE="${SANDBOX_SERVICE_CHART:-$TEST_DIR/sandbox-service.tgz}"
if [[ -z "${SANDBOX_SERVICE_CHART:-}" ]]; then
  curl --fail --location --silent --show-error --max-time 60 "$CHART_URL" --output "$CHART_ARCHIVE"
fi
python3 - "$CHART_ARCHIVE" "$CHART_SHA256" <<'PY'
import hashlib
import pathlib
import sys

if hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit("Sandbox Service chart checksum mismatch")
PY
tar -xzf "$CHART_ARCHIVE" -C "$TEST_DIR"

helm unittest \
  --values "$ROOT_DIR/braintrust/examples/eks-loop-runtime/sandbox-service.yaml" \
  --file "$ROOT_DIR/tests/sandbox-service_test.yaml" \
  "$TEST_DIR/sandbox-service"
helm lint "$TEST_DIR/sandbox-service" --strict \
  --values "$ROOT_DIR/braintrust/examples/eks-loop-runtime/sandbox-service.yaml"
