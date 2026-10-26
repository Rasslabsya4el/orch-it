"""More ways to delete, move and stop that the trigger reads (#395 unit 2b item 4): destructive git
commands, perl and ruby bodies, PowerShell .NET calls and -EncodedCommand, rimraf and deno, and the
commands that destroy a file's content. Each asserts the ACTION yielded, not the guard's verdict:
"/work/other" stands for another worktree, "/work/self" for the session's own."""

import base64

import pytest

from orch_it.guards import triggers
from orch_it.guards.triggers import Delete, Move, Stop, actions

OTHER = "/work/other"


def act(command, tool="Bash", cwd="/work/self", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def stops(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Stop)]


def named(command, rule, **kw):
    """The action of `rule` that `command` yields."""
    for a in act(command, **kw):
        if getattr(a, "rule_id", None) == rule:
            return a
    raise AssertionError(f"no {rule} from {command!r}: {act(command, **kw)}")


# --- git: the object is the worktree named by -C, else cwd -----------------------------------

@pytest.mark.parametrize("command,rule", [
    (f"git -C {OTHER} clean -fd", "git-clean"),
    (f"git -C {OTHER} clean -fdx", "git-clean"),
    (f"git -C {OTHER} clean --force", "git-clean"),
    (f"git -C {OTHER} reset --hard", "git-reset-hard"),
    (f"git -C {OTHER} reset --hard HEAD~3", "git-reset-hard"),
    (f"git -C {OTHER} checkout -- .", "git-checkout"),
    (f"git -C {OTHER} checkout .", "git-checkout"),
    (f"git -C {OTHER} checkout HEAD -- src", "git-checkout"),
    (f"git -C {OTHER} checkout -f", "git-checkout"),
    (f"git -C {OTHER} restore .", "git-checkout"),
    (f"git -C {OTHER} restore src/a.py", "git-checkout"),
    (f"git -C {OTHER} rm -r src", "git-rm"),
    (f"git -C {OTHER} rm -f a.py", "git-rm"),
])
def test_a_destructive_git_command_aimed_at_another_worktree_is_a_delete_there(command, rule):
    found = named(command, rule)
    assert isinstance(found, Delete)
    assert all(t is not None and (t == OTHER or t.startswith(OTHER + "/")) for t in found.targets), found
    assert found.recursive and not found.roots_too  # the worktree's own root is never deleted


def test_git_mv_is_a_move_inside_the_named_worktree():
    found = named(f"git -C {OTHER} mv a.py b.py", "git-mv")
    assert isinstance(found, Move) and found.sources == (f"{OTHER}/a.py",)
    assert named("git mv a.py b.py", "git-mv").sources == ("/work/self/a.py",)


def test_a_destructive_git_command_without_c_targets_cwd():
    assert named("git clean -fd", "git-clean").targets == ("/work/self",)
    assert named("git reset --hard", "git-reset-hard").targets == ("/work/self",)
    assert named("git rm -r src", "git-rm").targets == ("/work/self/src",)
    assert named("git checkout -- .", "git-checkout").targets == ("/work/self",)


def test_git_c_relative_to_cwd_and_chained_c_options():
    assert named("git -C ../other clean -fd", "git-clean").targets == (OTHER,)
    assert named("git -C /work -C other reset --hard", "git-reset-hard").targets == (OTHER,)
    assert named("git --work-tree=/work/other reset --hard", "git-reset-hard").targets == (OTHER,)
    assert named("git -C $(pick) clean -fd", "git-clean").targets == (None,)


@pytest.mark.parametrize("command", [
    "git clean -n", "git clean -nfd", "git clean --dry-run -fd", "git reset --soft HEAD~1",
    "git reset HEAD file", "git checkout main", "git checkout -b topic", "git checkout -b topic main",
    "git restore --staged a.py", "git restore", "git rm --cached a.py", "git mv --help",
    "git status", "git log", "git diff", "git add .", "git commit -m x", "git clean --help",
])
def test_a_git_command_that_destroys_nothing_yields_nothing(command):
    assert act(command) == []


# --- perl and ruby bodies --------------------------------------------------------------------

@pytest.mark.parametrize("command,rule", [
    (f"perl -e 'unlink \"{OTHER}/f\"'", "perl-e"),
    (f"perl -e \"unlink('{OTHER}/f')\"", "perl-e"),
    (f"perl -e \"use File::Path qw(rmtree); rmtree('{OTHER}')\"", "perl-e"),
    (f"perl -MFile::Path=remove_tree -e \"remove_tree('{OTHER}')\"", "perl-e"),
    (f"perl -E 'unlink \"{OTHER}/f\"'", "perl-e"),
    (f"ruby -e \"require 'fileutils'; FileUtils.rm_rf('{OTHER}')\"", "ruby-e"),
    (f"ruby -e \"FileUtils.rm_r '{OTHER}'\"", "ruby-e"),
    (f"ruby -e \"FileUtils.rm('{OTHER}/f')\"", "ruby-e"),
    (f"ruby -e \"File.delete('{OTHER}/f')\"", "ruby-e"),
    (f"ruby -e \"File.unlink('{OTHER}/f')\"", "ruby-e"),
])
def test_a_perl_or_ruby_delete_names_its_path(command, rule):
    found = named(command, rule)
    assert isinstance(found, Delete)
    assert any(t is not None and t.startswith(OTHER) for t in found.targets), found


def test_a_perl_or_ruby_delete_with_a_computed_path_is_not_refused():
    # a computed path (a variable) is unreadable, so it is not guessed at — like `os.rmdir(p)`,
    # it passes rather than blocking ordinary code; only a literal path is judged
    assert [a for a in act("perl -e 'unlink $f'") if a.rule_id == "perl-e"] == []
    assert [a for a in act("ruby -e \"FileUtils.rm_rf(path)\"") if a.rule_id == "ruby-e"] == []


def test_a_perl_or_ruby_delete_with_a_literal_path_is_caught():
    assert named("perl -e 'unlink \"/work/other\"'", "perl-e").targets == ("/work/other",)
    assert named("ruby -e 'FileUtils.rm_rf(\"/work/other\")'", "ruby-e").targets == ("/work/other",)


@pytest.mark.parametrize("command", [
    "perl -e 'print \"unlink /work/other\"'",
    "perl -e 'print 1'",
    "perl -e '# unlink /work/other'",
    "ruby -e 'puts \"FileUtils.rm_rf /work/other\"'",
    "ruby -e 'puts [1,2].delete(1)'",
    "ruby -e 'x = {}; x.delete(:a)'",
    "perl script.pl",
])
def test_a_perl_or_ruby_look_alike_yields_nothing(command):
    assert act(command) == []


# --- PowerShell .NET calls and -EncodedCommand -----------------------------------------------

def ps(command, **kw):
    return act(command, tool="PowerShell", windows=True, **kw)


@pytest.mark.parametrize("command", [
    r"[IO.Directory]::Delete('C:\work\other', $true)",
    r"[System.IO.Directory]::Delete('C:\work\other')",
    r"[IO.File]::Delete('C:\work\other')",
    r"[System.IO.File]::Delete('C:\work\other')",
    r"$null = [IO.Directory]::Delete('C:\work\other', $true)",
])
def test_a_dotnet_delete_is_a_delete_of_its_path(command):
    found = [a for a in ps(command) if getattr(a, "rule_id", "") == "dotnet-delete"]
    assert found and found[0].targets == (r"C:\work\other",), ps(command)


def test_a_dotnet_delete_with_a_computed_path_is_unreadable():
    found = [a for a in ps("[IO.Directory]::Delete($p, $true)") if getattr(a, "rule_id", "") == "dotnet-delete"]
    assert found and found[0].targets == (None,)


def test_a_dotnet_kill_by_id_names_the_pid_and_otherwise_is_opaque():
    found = [a for a in ps("[Diagnostics.Process]::GetProcessById(4321).Kill()") if isinstance(a, Stop)]
    assert found and found[0].pids == (4321,) and found[0].rule_id == "dotnet-kill"
    found = [a for a in ps("[System.Diagnostics.Process]::GetProcessById(4321).Kill($true)")
             if isinstance(a, Stop)]
    assert found[0].pids == (4321,) and found[0].tree is True
    for command in ("(Get-Process notepad).Kill()", "$p.Kill()",
                    "[Diagnostics.Process]::GetProcessById($id).Kill()"):
        found = [a for a in ps(command) if isinstance(a, Stop)]
        assert found and found[0].opaque and found[0].rule_id == "dotnet-kill", command
        assert not [a for a in ps(command) if isinstance(a, Delete)], command


@pytest.mark.parametrize("command", [
    "Write-Host '.Kill() and [IO.Directory]::Delete(x)'",
    "Get-Process | Where-Object { $_.Name -eq 'x' }",
    "$list.Remove(3)",
    "[IO.Directory]::Exists('C:\\work\\other')",
    "[IO.File]::ReadAllText('C:\\work\\other\\f')",
    "# $p.Kill()",
])
def test_a_dotnet_look_alike_yields_nothing(command):
    assert ps(command) == []


def encoded(text):
    return base64.b64encode(text.encode("utf-16-le")).decode()


@pytest.mark.parametrize("flag", ["-EncodedCommand", "-enc", "-ec", "-e", "-encodedcommand"])
def test_an_encoded_command_is_decoded_and_parsed(flag):
    blob = encoded(r"Remove-Item -Recurse C:\work\other")
    for tool, windows in (("PowerShell", True), ("Bash", False), ("Bash", True)):
        out = [a for a in act(f"powershell -NoProfile {flag} {blob}", tool=tool, windows=windows)
               if isinstance(a, Delete)]
        assert out and out[0].targets == (r"C:\work\other",) and out[0].rule_id == "remove-item", (tool, out)
    out = deletes(f"pwsh -ExecutionPolicy Bypass {flag} {blob}")
    assert out and out[0].rule_id == "remove-item"


def test_an_encoded_command_that_stops_a_process_is_found():
    blob = encoded("Stop-Process -Id 99")
    assert stops(f"powershell -enc {blob}")[0].pids == (99,)


@pytest.mark.parametrize("blob", ["!!!notbase64!!!", "AAAA", "A", "/w=="])
def test_an_encoded_command_that_cannot_be_decoded_is_opaque(blob):
    out = deletes(f"powershell -enc {blob}")
    assert any(d.rule_id == "unreadable-command" and None in d.targets for d in out), out


def test_an_encoded_command_with_an_unreadable_blob_is_opaque():
    out = deletes("powershell -enc $BLOB")
    assert any(None in d.targets for d in out)


def test_an_encoded_command_that_is_harmless_yields_nothing():
    assert act(f"powershell -enc {encoded('Get-ChildItem C:/')}") == []


# --- rimraf and deno -------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    f"rimraf {OTHER}", f"npx rimraf {OTHER}", f"npx -y rimraf {OTHER}", f"pnpm dlx rimraf {OTHER}",
    f"bunx rimraf {OTHER}", f"npm exec rimraf {OTHER}", f"npx rimraf --glob {OTHER}",
    f"./node_modules/.bin/rimraf {OTHER}",
])
def test_rimraf_is_a_delete_of_its_path(command):
    found = named(command, "rimraf")
    assert isinstance(found, Delete) and found.targets == (OTHER,) and found.recursive


@pytest.mark.parametrize("command", [
    f"deno eval \"Deno.removeSync('{OTHER}', {{recursive: true}})\"",
    f"deno eval \"await Deno.remove('{OTHER}', {{recursive: true}})\"",
    f"node -e \"Deno.removeSync('{OTHER}')\"",
])
def test_a_deno_remove_is_a_delete_of_its_path(command):
    found = named(command, "deno-remove")
    assert found.targets == (OTHER,)


def test_a_rimraf_or_deno_look_alike_yields_nothing():
    assert act("echo rimraf /work/other") == []
    assert act("npm install rimraf") == []
    assert act("node -e \"console.log('Deno.removeSync')\"") == []
    assert act("rimraf --help") == []
    assert act("npx --version") == []


# --- content destroyers ----------------------------------------------------------------------

@pytest.mark.parametrize("command,rule", [
    (f"shred {OTHER}/f", "shred"),
    (f"shred -u -n 3 -z {OTHER}/f", "shred"),
    (f"shred --iterations=3 --remove {OTHER}/f", "shred"),
    (f"truncate -s 0 {OTHER}/f", "truncate"),
    (f"truncate --size=0 {OTHER}/f", "truncate"),
    (f"truncate -c -s0 {OTHER}/f", "truncate"),
    (f"dd if=/dev/zero of={OTHER}/f bs=1M count=1", "dd"),
    (f"sudo dd of={OTHER}/f if=/dev/urandom", "dd"),
])
def test_a_content_destroyer_names_the_one_file(command, rule):
    found = named(command, rule)
    assert isinstance(found, Delete) and found.targets == (f"{OTHER}/f",), found
    assert found.recursive is False


@pytest.mark.parametrize("command", [
    "dd if=/dev/zero bs=1M count=1", "truncate --help", "shred --help", "dd --help",
])
def test_a_content_destroyer_with_no_file_yields_nothing(command):
    assert act(command) == []


def test_a_content_destroyer_without_a_readable_file_is_opaque():
    assert named("shred $F", "shred").targets == (None,)
    assert named("truncate -s 0 $F", "truncate").targets == (None,)
    assert named("dd if=/dev/zero of=$F", "dd").targets == (None,)


def test_a_single_redirect_onto_an_existing_file_overwrites_it(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    path = victim.as_posix()
    for command in (f"echo hi > {path}", f"echo hi >{path}", f": > {path}", f"> {path}",
                    f"printf x 1> {path}", f"ls 2> {path}", f"echo hi >| {path}",
                    f"cat <<EOF > {path}\ndata\nEOF", f"echo a && echo b > '{path}'"):
        found = [a for a in act(command) if getattr(a, "rule_id", "") == "redirect-overwrite"]
        assert found and found[0].targets == (path,), (command, act(command))
        assert found[0].recursive is False and found[0].roots_too is True


def test_a_redirect_that_overwrites_nothing_yields_nothing(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    path = victim.as_posix()
    assert act(f"echo hi >> {path}") == []                       # append keeps the content
    assert act(f"echo hi > {tmp_path.as_posix()}/new.txt") == []  # a new file has nothing to lose
    assert act("echo hi > /dev/null") == []
    assert act("ls 2>&1") == []
    assert act("ls >&2") == []
    assert act(f"cat < {path}") == []
    assert act("diff <(echo a) <(echo b)") == []
    assert act("echo $((1 > 0))") == []


def test_a_relative_redirect_is_read_against_cwd(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    found = [a for a in act("echo hi > f.txt", cwd=str(tmp_path))
             if getattr(a, "rule_id", "") == "redirect-overwrite"]
    assert found and found[0].targets == ("f.txt",)


def test_a_redirect_in_a_wrapped_command_is_read(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    path = (tmp_path / "f.txt").as_posix()
    assert any(getattr(a, "rule_id", "") == "redirect-overwrite" for a in act(f"bash -c 'echo hi > {path}'"))


def test_the_new_rule_ids_are_in_the_closed_set():
    for rule in ("unreadable-command", "git-clean", "git-rm", "git-mv", "git-reset-hard", "git-checkout",
                 "perl-e", "ruby-e", "dotnet-delete", "dotnet-kill", "rimraf", "deno-remove", "shred",
                 "truncate", "dd", "redirect-overwrite"):
        assert rule in triggers.RULE_IDS, rule


@pytest.mark.parametrize("command", [
    "cat <<", "cat <<EOF", "cat <<EOF | bash", "bash <<'EOF'\nrm -rf", "python - <<EOF\nos.system(",
    "python -c \"import os; os.system('\"", "python -c \"subprocess.run([\"", "node -e \"exec(\"",
    "node -e \"child_process.spawn(\"", "perl -e 'unlink'", "ruby -e 'FileUtils.rm_rf'",
    "powershell -enc", "powershell -EncodedCommand", "git -C", "git clean -", "git checkout --",
    "shred", "truncate -s", "dd of=", "echo >", "echo > >(cat)", "> ", ">>", "$(", "[IO.Directory]::Delete(",
    "$p.Kill(", "case x in (", "function f() {", "cat <<-\x00H0\x00",
])
def test_the_new_readers_never_raise_on_malformed_input(command):
    act(command)
    act(command, tool="PowerShell", windows=True)
    act(command, windows=True)
