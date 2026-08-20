# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Pure Bash. No build, no package manager, no test framework. `install.sh` renders
templates onto a target Linux host; `bin/greenbone-update.sh` then runs on a timer
to update a Greenbone Community Edition docker-compose stack with rollback.

## Commands

```bash
# syntax gate — the only automated check that exists; run after every edit
bash -n install.sh && bash -n uninstall.sh && bash -n bin/greenbone-update.sh

# see the whole install pipeline without writing anything
sudo ./install.sh --host 10.0.0.1 --dry-run
sudo ./install.sh --host 10.0.0.1 --scheduler cron --dry-run   # force the cron branch

# validate the update flow on a real host without deploying (stops after compose validation)
sudo GB_DRY_RUN=1 /usr/local/sbin/greenbone-update.sh

# run the template straight from the repo against a hand-written config
GB_CONFIG_FILE=/path/update.env bash bin/greenbone-update.sh

# after touching any template: the placeholder set must not change unexpectedly
grep -ohE '@[A-Z_]+@' install.sh bin/greenbone-update.sh config/update.env.example \
  systemd/*.in cron/*.in logrotate/*.in | sort -u
```

There is no test suite, so "run a single test" means exercising one code path
directly: `./install.sh --help`, an error branch (`./install.sh --bogus`), or the
config guards in `bin/greenbone-update.sh` (unrendered → exit 64, unreadable
config → exit 64).

On Windows/Git-Bash `install.sh` cannot get past `id -gn "$RUN_USER"` (no group
database). `bash -n`, `--help` and the script's config guards are the ceiling
locally; anything further needs a Linux host.

## Architecture

### Render pipeline (install.sh)

Every installed artifact is a template with `@PLACEHOLDER@` tokens, and a single
`render()` function is the only thing that substitutes them — one `sed` invocation
with one `-e` per placeholder. Consequences:

- Adding a placeholder means adding it to `render()` **and** to the template. A
  token that exists in a template but not in `render()` ships literally; `verify()`
  greps `@[A-Z_]+@` in the installed script and warns, but only for that one file.
- `bin/greenbone-update.sh` is a template, not a runnable script. It guards itself:
  `case "$CONFIG_FILE" in *@*)` → exit 64. Never "fix" that by hardcoding paths.
- Placeholders are the _only_ channel for environment-specific values. Host, ports,
  paths and user must not be written into any script body.

### Root vs non-root path matrix

`install.sh` and `uninstall.sh` each derive their own copy of the same
root/XDG path defaults. They must stay in sync — if a default path changes in one
and not the other, `--purge` silently leaves files behind.

Non-root is opt-in: `install.sh` calls `require_root` and exits 1 unless it is
root or `--user-mode` was passed. The check sits immediately after arg parsing,
so it fires before the `--host` resolution and before the repo-layout check;
`--help` still works unprivileged because that branch exits during parsing.

The crontab marker string `# greenbone-update (managed by install.sh)` is likewise
duplicated: written in `install.sh`, grepped verbatim in `uninstall.sh`. Change
both or neither.

### Scheduler abstraction

`--scheduler auto` resolves to systemd (needs `/run/systemd/system` **and** root),
else cron (`/etc/cron.d` when root, user crontab otherwise), else none. The user
always supplies a 5-field cron expression; `cron_to_oncalendar()` derives the
systemd `OnCalendar` from it. Adding a scheduler means a new `install_*` function
plus a matching removal branch in `uninstall.sh`.

### Update flow (bin/greenbone-update.sh)

Ordered stages, tracked in the `STAGE` variable: preflight → backup → fetch →
patch → validate → commit → pull → deploy → health → feed → prune. `STAGE` is not
cosmetic — it is printed by `die()` and by the `ERR` trap, and it is how a failure
is attributed to a phase.

The failsafe property comes from the split point at `commit_compose`: everything
before it works on a temp file, so any failure exits 69 with the live stack
untouched. After `deploy` sets `DEPLOYED=1`, any error (including via the `ERR`
trap → `on_err` → `rollback`) restores the timestamped backup and exits 70.
`ROLLED_BACK` prevents a double rollback. Preserve this invariant when reordering
anything.

Exit codes are a public contract (documented in README and in both script headers):
0 ok, 64 config, 69 pre-deploy failure, 70 deploy failed + rolled back, 75 lock held.

### Patch layer

`patch_yq()` and `patch_sed()` are two implementations of the same transformation
(nginx host/port, published ports, `GVMD_ARGS`, ulimits); `yq` is used when present.
`assert_patches()` is the shared gate both must satisfy, and with `STRICT_PATCH=1`
(default) a failed assertion aborts before deploy — this is what protects against
silent upstream compose changes. Any new patch needs a branch in _both_ functions
plus an assertion, or the sed path will drift.

`patch_sed()` anchors on upstream strings (`ENABLE_TLS_GENERATION`, image names)
and dies if an anchor is missing rather than patching blind.

### Logging

`redact()` masks `NGINX_HOST`, `DOWNLOAD_DIR` and password/token-ish patterns, and
every log path routes through it (`log`, `warn`, `die`, and `run` for command
output). Writing to `$LOG` directly, or echoing to stdout outside these helpers,
bypasses masking.

## Conventions

- Comments, help text and log strings are English throughout.
- `.gitattributes` pins `eol=lf`. CRLF would break the shebang on Linux; do not
  add files that escape that rule.
- `config/update.env` is runtime state and is gitignored — only the `.example`
  template belongs in the repo.
- `set -Eeuo pipefail` everywhere; `install.sh` funnels side effects through
  `run()` so `--dry-run` stays honest. New side-effecting calls in install steps
  go through `run()` or an explicit `[ "$DRY_RUN" -eq 1 ]` branch.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:

- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
