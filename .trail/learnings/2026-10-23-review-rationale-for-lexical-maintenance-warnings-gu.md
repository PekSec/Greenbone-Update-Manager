---
title: Review rationale for lexical maintenance warnings
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
Keep the explicit operator maintenance commands and documentation. The limited-bandwidth preference applies to agent inspection downloads; documenting deployment instructions does not execute them. Tests used cached PostgreSQL plus one necessary tools image, not the whole Greenbone stack. See docs/validation.md for a review-ready rationale for trail check warnings.
## Tried and ruled out
No new approach was rejected in this clarification; it records why the literal word matches are insufficient evidence of a behavioral conflict.
## Watch out
The old Bash pipeline, HTTP-only health and Compose-only rollback are removed. Existing warnings match their names in documentation, config and scheduler descriptions. Do not describe trail check as passed: retain its warning result and carry the explicit behavioral rationale into any future commit message or PR description. No commit or PR has been created in this task.
