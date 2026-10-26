# Skills are copied into each repo, his plugin off there

Every repo running the layer holds a copy of each of Pocock's skills it uses, byte for byte from one pinned commit of
his upstream, with a manifest of each file's git blob hash, and its own `.claude/settings.json` switches both of his
plugin ids off, so a session in the repo runs the repo's copies whatever version of his plugin the machine has
(Rasslabsya4el/orchestration-layer#391, #397 decisions 1 and 2). A daily check moves the pin by pull request only: it
compares every file of every skill, gives each skill an update class, and lets a pull request merge by itself only
when no skill was renamed, removed or changed in its contract; the rest wait for the brain (decision 4). Copies in the
repo because a skill the layer leans on must not change under it on one machine and not another, or on the day his
plugin updates itself; the whole text compared because the old check by names and headings let a one-line change
inside a skill through.

## Considered Options

- **Keep his plugin, pinned through a marketplace the repo registers**: documented only in parts and untested; it
  still loads every one of his skills, the hidden ones included, and a machine's own copy of the plugin can stay on
  beside it.
- **Copies in the user's personal skills folder**: binds the layer to one machine and one user, and a personal skill
  outranks a project skill anyway, so a repo could not hold its own version.
- **Rewritten skills of ours instead of his**: each rewrite has to earn its difference (#381); his text stays his, and
  the layer routes around it in its roles.

## Consequences

- **A personal skill of the same name still wins.** Claude Code ranks personal over project skills, so the layer does
  not prevent it: a session-start hook writes one `skill.shadowed` event per clash (decision 3).
- **Skills are named bare.** The copies call each other by bare name, and so do the roles; the plugin's namespaced
  names are refused by the references test, which also turns an update pull request red while a renamed or removed
  skill is still named somewhere (decision 5).
- **The hidden and the `no job` skills are not copied**, but the manifest lists them with why and their hashes, so a
  new skill upstream is told from one always there, and a change to a left-out skill still reaches the brain.
- **An added skill is copied and called by no role** until the brain assigns it.
