---
title: Publish the maintenance change after operator authorization
author: Güven
date: 2026-10-23
branch: main
touches:
  - AGENTS.md
  - CLAUDE.md
  - README.md
  - bin/greenbone-update.sh
  - config/update.env.example
  - cron/greenbone-update.cron.in
  - install.sh
  - logrotate/greenbone-update.in
  - systemd/greenbone-update.service.in
  - systemd/greenbone-update.timer.in
---

## Decided
After implementation and validation, the operator explicitly requested committing and pushing the maintenance change to main. Publish the maintenance files and task-authored team learnings. Preserve unrelated pre-existing AGENTS.md, CLAUDE.md and Trail setup changes outside the commit.
## Tried and ruled out
No further implementation or Docker downloads are needed for this publication step.
## Watch out
Earlier learning statements about no commit or push describe the implementation phase before this authorization. Carry the existing lexical-check rationale into the commit message. Unit tests, syntax and ShellCheck were rerun; the full Greenbone VM pilot remains unvalidated.
