# Skills are copied into each repo, his plugin off there

Every repo running the layer holds a copy of each of Matt Pocock's skills it uses, byte for byte from one pinned commit
of his upstream, with a manifest of each file's git blob hash, and its own `.claude/settings.json` switches both ids of
his plugin off, so a session in the repo runs the repo's copies whatever version of his plugin the machine has
(Rasslabsya4el/orchestration-layer#391; #397 decisions 1 and 2). The pin moves only by pull request: a workflow runs
`skills update` daily and on demand; the update compares every file of every skill with the manifest and gives each
skill an update class, and its pull request lists every skill with its class; one with a skill renamed, removed or
changed in its contract carries the label `needs-triage` and waits for the brain (the session that writes and judges
tickets), any other merges when green (decision 4). Copies in the repo because a skill the layer leans on must not
change under it on one machine and not another, or on the day his plugin updates itself; the whole text compared
because the earlier check by names and headings let a one-line change inside a skill through.

The project `false` was checked on the machine whose user settings enable his plugin: a session in the repo listed none
of the plugin's skills and loaded the repo's copy of `tdd` (orch-it pull request #17; without the project setting the
same session listed 27 of them).

## Considered Options

- **Keep his plugin, pinned through a marketplace the repo registers** (#390, the loading facts): put together from
  parts of the documentation and never run; it loads every one of his skills, those the layer leaves out included, and
  the copy of the plugin a machine holds itself stays on beside it.
- **Copies in the user's personal skills folder**: binds the layer to one machine and one user, and a personal skill
  outranks a project skill, so a repo could not hold its own version.
- **Skills of ours rewritten in place of his**: each rewrite has to earn its difference (#381); his text stays his, and
  the layer routes around it in its roles.

## Consequences

- **Undoing it is cheap in one repo and dear across them.** The copies, the manifest and the setting come off in one
  revert, but every repo the layer is installed in carries them (part 8), and the roles name skills by the copies' bare
  names.
- **Skills of ours sit beside the copies.** A folder the manifest does not name is ours: the update never writes or
  removes it.
- **A personal skill of the same name still wins.** Claude Code ranks personal over project skills, so the layer does
  not prevent it: a session-start hook writes one `skill.shadowed` event for each, and never blocks the session
  (decision 3).
- **Skills are named bare.** The copies call each other by bare name, and so do the roles; the references test refuses
  the plugin's namespaced names, and turns an update pull request red while a renamed or removed skill is still named
  somewhere (decision 5).
- **The skills the layer leaves out are not copied** (those the #390 table hides, and those with no job there), but the
  manifest lists them with why and their hashes, so a new skill upstream is told from one always there, and a rename,
  removal or contract change of one still waits for the brain.
- **An added skill is copied and called by no role** until the brain assigns it.
