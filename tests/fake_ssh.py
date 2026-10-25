"""A stand-in for `ssh` in tests: ignores the ssh options and the host (it takes the last
argument for the remote command), runs that command locally through the platform's shell, as a
sshd would, and passes stdin and stdout through.

With FAKE_SSH_DROP_WHEN_FILE set it plays a dropped connection: once the remote prints its
START line and the named file exists, it dies at once (no goodbye), so the remote sees its
stdin close.
"""

import os
import subprocess
import sys
import threading
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pump_stdin(child):
    try:
        while chunk := sys.stdin.buffer.read1(65536):
            child.stdin.write(chunk)
            child.stdin.flush()
    except (OSError, ValueError):
        pass
    try:
        child.stdin.close()
    except OSError:
        pass


def main():
    drop_file = os.environ.get("FAKE_SSH_DROP_WHEN_FILE")
    child = subprocess.Popen(sys.argv[-1], shell=True, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    threading.Thread(target=pump_stdin, args=(child,), daemon=True).start()
    for line in child.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
        if drop_file and line.startswith(b"ORCH-IT START"):
            deadline = time.monotonic() + 60
            while not os.path.exists(drop_file) and time.monotonic() < deadline:
                time.sleep(0.05)
            os._exit(255)
    sys.exit(child.wait())


if __name__ == "__main__":
    main()
