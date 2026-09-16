"""IP Filing Ecosystem: the Python runner that executes the Patent Drafting Playbook.

Two layers, and the dependency only ever points one way.

**Foundation** - `config` -> `state`/`log` -> `dossier` -> `playbook`/`contracts` ->
`terminology`/`numerals`/`transport`. What a matter is on disk, what a reply must
look like, and how OpenClaw is invoked.

**Orchestration** - `prompt_builder` -> `calls` -> `gates`/`loopback`/`package` ->
`runner` -> `cli`. What runs, in what order, what stops for a human, and what is
written down afterwards. Caps are counters, gates are files, sequencing is a
list: nothing here is left to an agent's judgement.

Every module is importable without side effects; nothing touches the filesystem
or the network at import time.
"""
