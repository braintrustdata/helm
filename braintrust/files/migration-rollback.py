"""Remove the target revision's retained resources before Helm restores them.

Helm 3 cannot update a retained object absent from the current release manifest.
Delete only the named target revision; foreground Job deletion waits for its
migration pods to terminate before the rollback can recreate that Job.
"""
import json
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, message, headers, response)


class Client:
    def __init__(self):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self.base = f"https://{host}:{os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')}"
        self.credentials = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(
                cafile=str(self.credentials / "ca.crt"))),
        )

    def request(self, method, path, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("rollback migration cleanup timed out")
        token = (self.credentials / "token").read_text().strip()
        payload = json.dumps({"propagationPolicy": "Foreground"}).encode() if method == "DELETE" else None
        request = urllib.request.Request(self.base + path, method=method, data=payload,
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=min(5, remaining)):
                return True
        except urllib.error.HTTPError as error:
            missing = error.code == 404
            error.close()
            if missing:
                return False
            # Do not log response bodies or credentials.
            raise RuntimeError(f"Kubernetes cleanup {method} returned HTTP {error.code}") from None


def resource_paths(namespace, name):
    namespace = urllib.parse.quote(namespace, safe="")
    name = urllib.parse.quote(name, safe="")
    return [
        f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}",
        f"/api/v1/namespaces/{namespace}/configmaps/{name}-config",
        f"/api/v1/namespaces/{namespace}/configmaps/{name}-gate",
        f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}/rolebindings/{name}",
        f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}/roles/{name}",
    ]


def cleanup(client, namespace, name, timeout, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    for path in resource_paths(namespace, name):
        client.request("DELETE", path, deadline)
        while client.request("GET", path, deadline):
            if clock() >= deadline:
                raise TimeoutError("rollback migration cleanup timed out")
            sleep(min(2, max(0, deadline - clock())))


if __name__ == "__main__":
    cleanup(Client(), os.environ["POD_NAMESPACE"], os.environ["MIGRATION_JOB_NAME"],
            int(os.environ["MIGRATION_TIMEOUT_SECONDS"]))
