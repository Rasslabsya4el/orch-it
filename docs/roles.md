# Roles

Each role of the layer is one agent file in `.claude/agents/`: its job, the inputs a spawn fills, its steps with the skill each step calls, its report and the events it writes. A role's model, effort and report rules are settings (`src/orch_it/roles/roles.json`); `python -m orch_it roles render` writes the model and effort into the file's frontmatter, and the text names none.

The rules that matter are checks, not sentences:

- `tests/test_role_texts.py` reads every role file, skill of ours, `CLAUDE.md` and doc, and fails on a dead path, a command, label, role or skill the layer does not have, a rule said in two places, a rule left to a local file, and a model or effort written in.
- `python -m orch_it spawn` starts a subagent with its inputs filled and refuses one whose input is missing.
- `python -m orch_it hook subagent-stop` holds the stop of a subagent whose report claims a skill it never called, edits code with no `tdd:` line, lacks a line its role owes, or runs past its cap.

## Every subagent

What every role started by a spawn does (all roles but the brain and ORCH, which are sessions):

1. Read each file your prompt names, whole, before your first step: the ticket file, and the unit's row or the diff file your role takes. They were written at spawn, so they are the ticket as it stood then; the ticket's agent block is your contract.
2. Work in the worktree your prompt names. Your own notes and probes go in the scratchpad it names.
3. A guard's refusal of a command is final: put the command and the guard's reason in your report and go on without it.
4. End with your report. Its first line is the result; then each finding or fact with its source (a file and line, a test id, a URL); then the report lines your role owes. The roles settings give its cap in lines.
5. A report line states a call that happened in this subagent: `tdd: called` after the Skill tool ran `tdd`, else `tdd: skipped (why)`. Every skill line reads the same way.
