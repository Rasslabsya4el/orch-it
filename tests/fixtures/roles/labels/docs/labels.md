# Labels

A ticket the owner pushes gets the `priority` label; one an agent can take is `ready-for-agent`.

Mark a stuck ticket with `gh issue edit 12 --add-label stuck,ready-for-agent --remove-label needs-info`.

Open a ticket with `gh issue create --title T --body-file b.md -l needs-triage --label "hard push"`.

The label `autopilot` and the `wontfix` label; a `reviewer-spec` role and the role `brain`.

The `dispatcher` role starts sessions.
