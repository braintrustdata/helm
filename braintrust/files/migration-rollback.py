"""Remove the target revision's retained resources before Helm restores them.

Helm 3 cannot update a retained object absent from the current release manifest.
Delete only the named target revision; foreground Job deletion waits for all of
its migration and rollout pods before rollback can recreate either Job.
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
            raise TimeoutError("rollback release cleanup timed out")
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


def resource_paths(namespace, names):
    namespace = urllib.parse.quote(namespace, safe="")
    names = [urllib.parse.quote(name, safe="") for name in names]
    jobs = [f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}" for name in names]
    retained = []
    for name in names:
        retained.extend([
            f"/api/v1/namespaces/{namespace}/configmaps/{name}-config",
            f"/api/v1/namespaces/{namespace}/configmaps/{name}-gate",
            f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}/rolebindings/{name}",
            f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}/roles/{name}",
        ])
    return jobs + retained


def cleanup(client, namespace, names, timeout, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    paths = resource_paths(namespace, names)
    jobs, retained = paths[:len(names)], paths[len(names):]

    def check_deadline():
        if clock() >= deadline:
            raise TimeoutError("rollback release cleanup timed out")

    def request(method, path):
        check_deadline()
        exists = client.request(method, path, deadline)
        check_deadline()
        return exists

    def wait_until_deleted(path):
        while request("GET", path):
            sleep(min(2, max(0, deadline - clock())))

    # Stop both phases before removing any config or permissions they rely on.
    for path in jobs:
        request("DELETE", path)
    for path in jobs:
        wait_until_deleted(path)
    for path in retained:
        request("DELETE", path)
        wait_until_deleted(path)


def main():
    names = json.loads(os.environ["RELEASE_JOB_NAMES"])
    if not isinstance(names, list) or not names or any(
            not isinstance(name, str) or not name for name in names):
        raise ValueError("RELEASE_JOB_NAMES must be a nonempty list of Job names")
    timeout = int(os.environ["RELEASE_TIMEOUT_SECONDS"])
    if timeout <= 0:
        raise ValueError("RELEASE_TIMEOUT_SECONDS must be positive")
    cleanup(Client(), os.environ["POD_NAMESPACE"], names, timeout)


if __name__ == "__main__":
    main()
