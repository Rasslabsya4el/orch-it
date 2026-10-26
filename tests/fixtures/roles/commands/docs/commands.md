# Commands

Sync the labels with `python -m orch_it labels sync --dry-run`.

Render the roles:

```bash
python -m orch_it roles render --work-tree . && python -m orch_it roles check
```

The ticket hook runs as `python -m orch_it.tickets.nohook`; the real one is `python -m orch_it.tickets.hook`.

Read a store with `python -m orch_it [--store PATH] events query "<sql>" [--param NAME=VALUE ...]`, then `python -m orch_it tests run --os plan9`.

Name a placeholder: `python -m orch_it tickets park <n> <kind> <ref> --until <YYYY-MM-DD>`, or `python -m orch_it events <command>`.
