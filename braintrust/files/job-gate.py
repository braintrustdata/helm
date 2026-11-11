"""Wait for the exact release Job; never infer success from absence."""

import os
import sys
import time
import urllib.error
import urllib.parse

from brainstore_gate import KubernetesClient


def job_complete(job):
    conditions = job.get("status", {}).get("conditions", [])
    if any(c.get("type") in {"Failed", "FailureTarget"} and c.get("status") == "True"
           for c in conditions):
        raise RuntimeError("Job failed; inspect its logs and retry with a new Helm upgrade")
    if job.get("metadata", {}).get("deletionTimestamp"):
        return False
    return any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)


def wait_for_job(client, name, timeout, interval,
                 clock=time.monotonic, sleep=time.sleep, log=print):
    deadline = clock() + timeout
    path = f"/apis/batch/v1/namespaces/{client.namespace}/jobs/{urllib.parse.quote(name, safe='')}"
    reason = "Job has not completed"
    while clock() < deadline:
        try:
            complete = job_complete(client.get(path, deadline))
            if complete and clock() < deadline:
                log(f"Job {name} completed successfully", flush=True)
                return
            reason = "Job has not completed"
        except urllib.error.HTTPError as error:
            # Job and RBAC may be applied after the Deployment, including 403/404.
            reason = f"Kubernetes API returned HTTP {error.code}"
            error.close()
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = f"Kubernetes observation failed: {type(error).__name__}"
        log(f"Waiting for Job {name}: {reason}", flush=True)
        sleep(min(interval, max(0, deadline - clock())))
    raise TimeoutError(f"Job {name} wait timed out after {timeout}s: {reason}")


def main():
    timeout = int(os.environ["JOB_TIMEOUT_SECONDS"])
    interval = int(os.environ["JOB_POLL_SECONDS"])
    name = os.environ["JOB_NAME"]
    if not name or interval <= 0 or timeout <= interval:
        raise ValueError("invalid Job gate configuration")
    client = KubernetesClient(os.environ["POD_NAMESPACE"], "/var/run/migration-gate")
    wait_for_job(client, name, timeout, interval)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
