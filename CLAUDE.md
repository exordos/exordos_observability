# CLAUDE.md

This project's context is kept tool-agnostic so it stays useful outside Claude Code too. Full context lives in:

@AGENTS.md
@docs/DESIGN.md

Read `docs/DESIGN.md` in full before making non-trivial changes — it lists exactly what's been built, what's still unverified, and the risks to check before extending this repo. `AGENTS.md` has day-to-day build/test/lint commands and coding conventions.

## Claude-Code-specific notes

- This repo has no `.claude/skills/` or custom agents of its own yet — none were needed for the initial scaffold. If you add repo-specific automation (e.g. a skill for regenerating a plugin's migration file), document it here.
- When exploring the framework this repo builds on, prefer reading the actual sibling repos (`../exordos_metapaas`, `../exordos_s3`, `../exordos_db`, `../exordos_core/docs`) over relying on this file's summaries — they are the ground truth and may have moved on since `docs/DESIGN.md` was written.
- Follow this repo's existing license-header and code-style conventions (see `AGENTS.md`) rather than Claude Code's general defaults when they differ.
