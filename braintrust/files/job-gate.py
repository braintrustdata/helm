"""Wait for the current content-addressed migration Job; fail closed."""

import os
import sys
import time
import urllib.error

from brainstore_gate import KubernetesClient, load_ordering_config, migration_complete


def wait_for_job(client, configmap, timeout, interval,
                 clock=time.monotonic, sleep=time.sleep, log=print):
    deadline = clock() + timeout
    reason = "no observation yet"
    while clock() < deadline:
        stage = "ordering configuration"
        try:
            ordering = load_ordering_config(client, configmap, deadline)
            name = ordering["job"]
            stage = f"Job {name}"
            complete = migration_complete(client, name, deadline)
            if complete and clock() < deadline:
                log(f"Job {name} completed successfully", flush=True)
                return
            reason = f"Job {name} has not completed"
        except urllib.error.HTTPError as error:
            # Job and RBAC may be applied after the Deployment, including 403/404.
            reason = f"{stage}: Kubernetes API returned HTTP {error.code}"
            error.close()
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = f"{stage}: Kubernetes observation failed: {type(error).__name__}"
        log(f"Waiting for migration Job: {reason}", flush=True)
        sleep(min(interval, max(0, deadline - clock())))
    raise TimeoutError(f"Migration Job wait timed out after {timeout}s: {reason}")


def main():
    timeout = int(os.environ["JOB_TIMEOUT_SECONDS"])
    interval = int(os.environ["JOB_POLL_SECONDS"])
    configmap = os.environ["ORDERING_CONFIGMAP"]
    if not configmap or interval <= 0 or timeout <= interval:
        raise ValueError("invalid Job gate configuration")
    client = KubernetesClient(os.environ["POD_NAMESPACE"], "/var/run/migration-gate")
    wait_for_job(client, configmap, timeout, interval)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
