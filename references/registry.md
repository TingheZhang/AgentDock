# Registry and project operations

The registry is normally `platform/ports.json`, discovered from the selected
platform root. Define it explicitly, for example:

```bash
export PLATFORM_ROOT=/data/lisy/MyTask
export VCC_REGISTRY="$PLATFORM_ROOT/platform/ports.json"
```

Read the file before editing. Confirm the project name, absolute task path,
`ui_port`, bind addresses, and `knowledge.note_subdirs`/`knowledge.wikis`.
The same project name must be unique. The current `bb-ports.sh add` command
appends a project and rewrites the registry; it exits if the name already
exists. Do not both hand-edit `projects[]` and run `add` for the same project.

Before any write, read the project `AGENTS.md`, check the task contract, and
obtain the user authorization required for that registry change. Preserve
existing entries and concurrent work. Refresh generated dashboard output only
through the installed platform command, and verify the actual HTTP endpoint.

The current service target is `vcc-platform.target`. Do not infer unit names
from an old document; inspect `systemctl --user list-unit-files` and the live
unit source. A single-project proxy refresh is preferable to a platform-wide
restart when the installed tool supports it.

Project task status is a claim until the task's specified commit/run/deploy/
artifact/acceptance evidence exists. Follow the project's merge and CHANGELOG
rules; do not mark work complete merely because a file or branch exists.
