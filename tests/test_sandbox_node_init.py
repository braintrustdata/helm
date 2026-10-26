import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


NODE_INIT = Path(__file__).resolve().parents[1] / "braintrust/examples/gke-loop-runtime/node-init.yaml"
SCRIPT = textwrap.dedent(NODE_INIT.read_text().split("  init.sh: |\n", 1)[1].split("\n---", 1)[0])

# Execute the deployed script, substituting only host/device operations.
HOST_COMMANDS = r"""
modprobe() { return 0; }
test() {
    if [[ "$1" == -c && "$2" == /dev/kvm ]]; then
        [[ "$KVM" == yes ]]
    else
        builtin test "$@"
    fi
}
mkdir() { return 0; }
readlink() { printf '%s\n' "$2"; }
mountpoint() { [[ "$MOUNTED" == yes ]]; }
blkid() { [[ -n "$FSTYPE" ]] && printf '%s\n' "$FSTYPE"; }
mkfs.xfs() { echo format-xfs >>"$LOG"; FSTYPE=xfs; }
mkfs.ext4() { echo format-ext4 >>"$LOG"; FSTYPE=ext4; }
mdadm() { echo raid >>"$LOG"; }
mount() { echo "mount ${@: -1}" >>"$LOG"; MOUNTED=yes; FSROOT=/; }
findmnt() {
    case "$3" in
        FSTYPE) printf '%s\n' "$FSTYPE" ;;
        FSROOT) printf '%s\n' "$FSROOT" ;;
    esac
}
"""


class SandboxNodeInitTests(unittest.TestCase):
    def run_init(self, filesystem="", mounted=False, fsroot="/", disks=1, kvm=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(disks):
                (root / f"disk-{index}").touch()
            log = root / "commands"
            env = {
                **os.environ,
                "DEVICES": str(root / "disk-*"),
                "FSTYPE": filesystem,
                "MOUNTED": "yes" if mounted else "no",
                "FSROOT": fsroot,
                "KVM": "yes" if kvm else "no",
                "LOG": str(log),
            }
            env.pop("ROOT", None)
            result = subprocess.run(
                ["/bin/bash", "-c", HOST_COMMANDS + SCRIPT],
                env=env, text=True, capture_output=True,
            )
            return result, log.read_text().splitlines() if log.exists() else []

    def test_fresh_ssd_mounts_xfs_directly_at_worker_state_path(self):
        result, commands = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, ["format-xfs", "mount /var/lib/braintrust-sandboxes/1"])

    def test_restart_and_reboot_preserve_existing_xfs(self):
        for mounted in (True, False):
            with self.subTest(mounted=mounted):
                result, commands = self.run_init("xfs", mounted=mounted)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(commands, [] if mounted else ["mount /var/lib/braintrust-sandboxes/1"])

    def test_existing_non_xfs_filesystem_is_never_reformatted(self):
        for mounted in (True, False):
            with self.subTest(mounted=mounted):
                result, commands = self.run_init("ext4", mounted=mounted)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(commands, [])

    def test_xfs_subdirectory_is_not_accepted_as_a_filesystem_root(self):
        result, commands = self.run_init("xfs", mounted=True, fsroot="/1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [])

    def test_multiple_ssds_are_striped_before_formatting(self):
        result, commands = self.run_init(disks=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, ["raid", "format-xfs", "mount /var/lib/braintrust-sandboxes/1"])

    def test_missing_ssd_or_kvm_prevents_disk_initialization(self):
        for options in ({"disks": 0}, {"kvm": False}):
            with self.subTest(options=options):
                result, commands = self.run_init(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()

