"""Helpers of the private-data scanner tests (#395): values the scanner must find, put together at
run time from parts so none of them stands in a committed file (this repository's own pull
requests are scanned by the step these tests cover), and a throwaway repository to commit them to."""

import os
import shutil
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
    and the machine's git configuration is out of it (and the real repository's is never read).

    `Repo(path)` runs `git init` and the configuration; `Repo(path, template=t)` copies a finished
    repository `t` (made once by `make_template`, never written after) instead, which costs no git
    process at all: the copy is as the template was, with its commits, so each test starts from it."""

    def __init__(self, path: Path, template: Path | None = None):
        self.path = path
        self.env = os.environ | {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": NOREPLY,
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": NOREPLY}
        if template is not None:
            shutil.copytree(template, path, ignore=shutil.ignore_patterns("*.sample"))
            return
        path.mkdir()
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

    def commits_on(self, base: str, specs) -> list[str]:
        """One commit per spec (`files`, `remove`, `message`, `author`, `committer`), each on `base`
        and none on HEAD; all made by one `git fast-import`, so the work tree and the index stay as
        they are (a test that pushes each of many commits in turn needs no commit or reset per case)."""

        marks = self.path / ".git" / "batch-marks"
        chunks: list[bytes] = []
        for number, spec in enumerate(specs, 1):
            message = spec.get("message", "work")
            message = message if message.endswith("\n") else message + "\n"
            stamp = f"{1_700_000_000 + number} +0000"
            head = (f"commit refs/batch/{number}\nmark :{number}\n"
                    f"author Fixture <{spec.get('author') or NOREPLY}> {stamp}\n"
                    f"committer Fixture <{spec.get('committer') or NOREPLY}> {stamp}\n"
                    f"data {len(message.encode('utf-8'))}\n")
            chunks += [head.encode("utf-8"), message.encode("utf-8"), f"from {base}\n".encode("utf-8")]
            for name, content in (spec.get("files") or {}).items():
                body = content if isinstance(content, bytes) else content.encode("utf-8")
                chunks += [f"M 100644 inline {name}\ndata {len(body)}\n".encode("utf-8"), body, b"\n"]
            chunks += [f"D {name}\n".encode("utf-8") for name in spec.get("remove", ())]
        self.git("fast-import", "--quiet", f"--export-marks={marks}", input=b"".join(chunks))
        found = dict(line.split(" ", 1) for line in marks.read_text("utf-8").splitlines())
        return [found[f":{number}"] for number in range(1, len(specs) + 1)]

    @property
    def head(self) -> str:
        """The commit HEAD names. Read from the repository's own files when it is a loose ref (what
        `git commit` writes), so a commit costs no `rev-parse` process; otherwise git is asked."""

        git_dir = self.path / ".git"
        text = (git_dir / "HEAD").read_text("utf-8").strip()
        if text.startswith("ref: ") and (git_dir / text[5:]).is_file():
            return (git_dir / text[5:]).read_text("utf-8").strip()
        return self.git("rev-parse", "HEAD")


def make_template(path: Path, files=None, message="work") -> tuple[Path, str]:
    """A repository at `path` (not yet there) that holds `files` as one commit, or none; its path
    and the commit's sha (`""` without files). Build it once per test session and `Repo(p,
    template=path)` each test from it, so no test writes into it."""

    template = Repo(path)
    base = template.commit(files, message=message) if files else ""
    return path, base
