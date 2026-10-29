"""The closed sets match the event list of #388 (sections 2, 3, 4, 8 of
docs/research/2026-10-25-event-list-355.md on branch research/event-list-355 of
the layer's repo). The expected names below are copied from that list
by hand, so this test is the check against it, not against the file itself."""

from orch_it.events import schema

ENVELOPE = [
    "id", "kind", "at", "repo", "ticket", "role", "session", "parent_session", "turn",
    "step", "run", "machine", "os", "layer_version", "note",
]

STEP_IDS = [
    # section 3
    "session", "subagent", "wait", "test_run", "review", "unit", "pr", "core_update",
    "install", "research_run", "process", "external_call", "check", "sweep", "test_map",
    "keepalive_install", "acceptance",
    # section 8
    "ticket_write", "launch", "claim", "plan", "close", "reconcile",
    "line",  # #400 decision 2
]

POINT_KINDS = [
    # section 4
    "turn.used", "test.result", "hook.ran", "run.checked", "ticket.transferred",
    "machine.booted", "tool.called", "skill.called", "report_line.checked", "rule_file.read",
    "context.crossed", "message.sent", "handoff.written", "handoff.read", "ticket.opened",
    "ticket.edited", "ticket.claimed", "ticket.released", "ticket.parked", "ticket.unparked",
    "park.unreadable", "comment.written", "attempt.counted", "ticket.closed",
    "ticket.reopened", "acceptance_row.checked", "review.finding", "owner.asked",
    "owner.answered", "launch.decided", "guard.fired", "guard.reversed", "error.raised",
    "pr.updated", "pr.conflicted", "pr.conflict_resolved", "pr.commented", "main.red",
    "main.reverted", "doc.edited", "term.missing", "drift.found", "setting.read",
    "value.measured", "view.read",
    # section 3 (progress of a long step) and section 8
    "step.progressed", "machine.resumed",
    # #397 decision 3
    "skill.shadowed",
    # #400 decision 2
    "lock.removed", "line.served",
    # #414 decision 9: one GitHub call counted per account per hour, with the budget read from GitHub when known
    "github.called",
]

OUTCOMES = ["passed", "failed", "no_result", "refused", "lost", "stopped"]

# (kind or step side, field) -> the codes the list writes next to the field
CODE_FIELDS = {
    ("envelope", "role"): {
        "owner", "brain", "orch", "worker", "worker-strong", "reviewer-standards",
        "reviewer-spec", "reviewer-edge", "researcher-find", "researcher-how", "researcher-survey",
        "external-researcher", "launcher", "github", "hook", "script",
    },
    ("session.started", "launched_by"): {"launcher", "owner", "session", "boot"},
    ("session.ended", "reason"): {
        "turn_ended", "idle", "context_ceiling", "killed", "outage", "stopped_by_owner",
        "crashed", "suspended", "lease_lost",
    },
    ("subagent.ended", "delivered"): {"commit", "comment", "file", "none"},
    ("wait.started", "reason"): {
        "test_place", "rate_limit", "owner_answer", "brain_answer", "parked", "blocker",
        "review", "github_queue", "capacity",
    },
    ("test_run.started", "place"): {"own", "machine:<id>", "github"},
    ("review.started", "axis"): {"standards", "spec", "edge"},
    ("unit.ended", "reason"): {"format", "result", "merge", "suspended", "lease_lost"},
    ("attempt.counted", "reason"): {"red", "red_on_main", "no_result", "conflict"},  # #400 decision 3
    ("pr.ended", "reason"): {
        "merged", "red", "closed_unmerged", "timeout", "withdrawn", "conflict", "suspended", "lease_lost",
    },
    ("external_call.started", "target"): {"gh", "git", "github_api", "bridge_http", "claude_cli"},
    ("check.started", "check"): {
        "pocock_contract", "leak", "guard_install", "branch_protection", "hooks_path",
        "environment",
    },
    ("claim.ended", "reason"): {"held_by_other", "not_ready", "suspended", "lease_lost"},
    ("plan.ended", "reason"): {"ticket_changed", "not_doable", "suspended", "lease_lost"},
    ("close.ended", "reason"): {
        "acceptance_missing", "row_failed", "unanswered", "already_closed", "ticket_changed", "suspended",
        "lease_lost",
    },
    ("reconcile.ended", "repairs"): {
        "lease_released", "closed", "unparked", "rerun", "red_ticket_opened",
    },
    # #394 decision 2: the reason of each test run outcome; decision 5: the place rule's choice
    ("test_run.ended", "reason"): {
        "red", "cut", "none_collected", "ids_not_collected", "summary_missing", "job_missing",
        "busy", "os_missing", "place_off", "out_of_minutes", "place_vanished", "superseded",
        "suspended", "lease_lost",
    },
    ("test_run.started", "reason"): {"earliest_finish", "nothing_measured", "forced"},
    ("test_run.started", "chosen"): {"own", "machine:<id>", "github"},
    ("test_run.started", "run_os"): {"linux", "windows", "macos"},
    ("turn.used", "reason"): {"price_missing", "usage_unreadable"},  # decision 6; a turn whose usage is no counts
    ("test.result", "load_verdict"): {"code", "load"},
    ("hook.ran", "action"): {"acted", "skipped"},
    ("run.checked", "result"): {"alive", "ended", "failed"},
    ("tool.called", "outcome"): {"passed", "failed", "refused"},
    ("tool.called", "refused_by"): {
        "worktree_delete", "foreign_process_stop", "main_push_no_green", "private_data_public",
        "permission",
    },
    ("report_line.checked", "label"): {
        "tdd", "diagnosis", "code-review", "research", "writing-for-agents",
        "domain-modeling", "codebase-design", "report_cap", "table",
    },
    ("context.crossed", "threshold"): {"context_line", "ceiling"},
    ("context.crossed", "action_after"): {"handed_off", "new_work_taken", "told"},
    ("message.sent", "kind"): {"notice", "question", "answer", "handoff", "report"},
    ("ticket.edited", "field"): {
        "title", "body", "labels", "weight", "priority", "assignee", "parent", "blocked_by",
    },
    ("ticket.released", "reason"): {"closed", "parked", "handed_off", "session_ended"},
    ("ticket.parked", "on"): {
        "pr_ended", "ticket_closed", "date", "run_ended", "owner_answer", "layer_version",
    },
    ("ticket.unparked", "by"): {"event", "hand"},
    ("comment.written", "comment_kind"): {
        "checkpoint", "send_back", "accepted", "owner_answer", "brain_answer", "resolution",
        "closing", "machine",
    },
    ("ticket.closed", "via"): {"close_command", "github_keyword", "manual"},
    ("ticket.closed", "state_reason"): {"completed", "not_planned"},
    ("acceptance_row.checked", "result"): {"pass", "fail", "na"},
    ("review.finding", "axis"): {"standards", "spec", "edge"},
    ("review.finding", "fate"): {"fixed", "dismissed", "open"},
    ("launch.decided", "result"): {"started", "refused"},
    ("guard.fired", "guard"): {
        "worktree_delete", "foreign_process_stop", "main_push_no_green", "private_data_public",
    },
    ("guard.fired", "verdict"): {"deny", "pass"},
    ("doc.edited", "doc_kind"): {
        "glossary", "adr", "state", "role_file", "skill", "core_block", "research", "memory",
        "config",
    },
    ("drift.found", "drift"): {
        "behind", "hook_extra", "hook_missing", "file_edited_in_target", "leak",
    },
    ("setting.read", "source"): {"repo", "machine", "default"},
    ("machine.resumed", "cause"): {"suspend", "hibernate"},
    ("github.called", "budget"): {"account", "actions"},
}


def test_envelope_is_the_list_s_fifteen_fields():
    assert list(schema.envelope_fields()) == ENVELOPE


def test_every_step_id_of_the_list_and_no_other():
    assert sorted(schema.step_ids()) == sorted(STEP_IDS)
    assert len(STEP_IDS) == 24  # + line (#400)


def test_every_point_kind_of_the_list_and_no_other():
    assert sorted(schema.point_kinds()) == sorted(POINT_KINDS)
    assert len(POINT_KINDS) == 51  # 47 of the list, + github.called (#414), + skill.shadowed (#397), lock.removed, line.served (#400)


def test_kinds_are_the_point_kinds_and_the_pair():
    assert sorted(schema.kinds()) == sorted(POINT_KINDS + ["step.started", "step.ended"])


def test_the_six_outcomes():
    assert schema.outcomes() == OUTCOMES


def test_every_code_list_written_next_to_its_field():
    for (owner, field), codes in CODE_FIELDS.items():
        assert schema.codes_of(owner, field) == codes, (owner, field)
