# A role's rules are held by checks, and its subagent starts only through spawn

The role files are written fresh, each with its job, inputs, steps, report and events, and every rule that matters is a check that fails, not a sentence (Rasslabsya4el/orchestration-layer#399, decisions 1, 3-6): one test reads every role file, skill of ours, CLAUDE.md and doc for #382's kinds of break; `python -m orch_it spawn` fills a subagent's inputs from one place and refuses one that lacks an input; a SubagentStop hook holds a report whose skill lines have no call behind them; each role's model, effort, fallback model and report rules are one shared settings file, its model and effort written into the files' frontmatter and its fallback read by the launcher. Checks, because the old layer's 30 files held 461 broken rows (#382) and its rules held where a hook stood and were skipped where a sentence stood (#380, #381); spawn, because the old delegation prompt was free text and a review without its diff reported "0 findings".

## Considered Options

- **The old files, mended**: 254 of their 461 broken rows named a part the layer removed; fixing them row by row keeps the rule-in-a-sentence shape that #380 measured as skipped.
- **A list of files the checks read**: a role added later would pass unread; the checks read whatever role files exist.
- **The model in each file by hand**: a model is a setting the launcher and the price table also read, so it lives once.

## Consequences

- **A rule two files state is red.** One file states it and the others point at it; the ADRs are left out, since they record why a rule is so.
- **The brain's own file is the owner's.** It is drafted in its own pull request and merged only on the owner's yes.
- **Known gaps.** Two rules on one object that disagree in different words are not seen by the word check; the stop hold judges a report only at its stop, once, so a survey past its context line is told at its stop, not before.
