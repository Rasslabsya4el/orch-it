"""A stand-in for `ssh` in tests: ignores the ssh options and the host (it takes the last
argument for the remote command), runs that command locally through the platform's shell, as a
sshd would, and passes stdin and stdout through.

The shell is the platform's, handed the command as a sshd hands it: `sh -c <command>`, and on
Windows `cmd.exe /c <command>` with the command unquoted by anyone else, as Windows OpenSSH does.

Playing up, by environment variable:
- FAKE_SSH_DROP_WHEN_FILE: a dropped connection: once the remote prints its START line and the
  named file exists, it dies at once (no goodbye), so the remote sees its stdin close.
- FAKE_SSH_CUT_REPORT: it dies halfway through the REPORT line, with no newline.
- FAKE_SSH_BANNER: a banner without a newline comes before the remote's first line.
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


def shell_command(command):
    if sys.platform == "win32":
        return f"{os.environ.get('COMSPEC', 'cmd.exe')} /c {command}"
    return ["/bin/sh", "-c", command]


def main():
    drop_file = os.environ.get("FAKE_SSH_DROP_WHEN_FILE")
    child = subprocess.Popen(shell_command(sys.argv[-1]), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    threading.Thread(target=pump_stdin, args=(child,), daemon=True).start()
    if os.environ.get("FAKE_SSH_BANNER"):
        sys.stdout.buffer.write(b"Welcome to the fake host ")
    for line in child.stdout:
        if os.environ.get("FAKE_SSH_CUT_REPORT") and line.startswith(b"ORCH-IT REPORT"):
            sys.stdout.buffer.write(line[:len(line) // 2])
            sys.stdout.buffer.flush()
            os._exit(255)
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
