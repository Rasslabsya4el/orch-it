"""Helpers of the private-data scanner tests (#395): values the scanner must find, put together at
run time from parts so none of them stands in a committed file (this repository's own pull
requests are scanned by the step these tests cover), and a throwaway repository to commit them to."""

import os
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NOREPLY = "fixture@users.noreply.github.com"

GITHUB_TOKEN = "gh" + "p_" + "a1B2" * 9
GITHUB_FINE_TOKEN = "github" + "_pat_" + "Ab1_" * 6
ANTHROPIC_KEY = "sk" + "-ant-" + "api03-" + "Zy9" * 6
OPENAI_KEY = "sk" + "-" + "proj" + "Qw3" * 8
AWS_KEY = "AK" + "IA" + "ABCDEFGH12345678"
PRIVATE_KEY = "-----" + "BEGIN RSA PRIVATE" + " KEY-----"
EMAIL = "jane.doe" + "@" + "corp" + ".io"
SOMEONE_HOME = "/ho" + "me/someone"
MAC_HOME = "/Us" + "ers/someone"
WIN_HOME = "C:" + "\\Us" + "ers\\someone"


class Repo:
    """A repository in `tmp_path` whose own configuration names a noreply identity; the user's
    and the machine's git configuration is out of it (and the real repository's is never read)."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir()
        self.env = os.environ | {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": NOREPLY,
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": NOREPLY}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", NOREPLY)
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args, check=True, input=None, env=None) -> str:
        done = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, input=input,
                              timeout=120, creationflags=NO_WINDOW, env=env or self.env)
        assert not check or done.returncode == 0, done.stderr.decode("utf-8", "replace")
        return done.stdout.decode("utf-8", "replace").strip()

    def commit(self, files=None, message="work", *, author=None, committer=None, remove=()) -> str:
        """Write `files` (path -> text or bytes), remove `remove`, commit all; the full sha."""

        for name, content in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        for name in remove:
            (self.path / name).unlink()
        self.git("add", "-A")
        env = self.env | ({"GIT_AUTHOR_EMAIL": author} if author else {}) | (
            {"GIT_COMMITTER_EMAIL": committer} if committer else {})
        self.git("commit", "-q", "--allow-empty", "--cleanup=verbatim", "-m", message, env=env)
        return self.head

    @property
    def head(self) -> str:
        return self.git("rev-parse", "HEAD")
