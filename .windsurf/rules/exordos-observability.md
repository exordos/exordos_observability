---
trigger: always_on
description: Exordos Observability MetaPaaS plugin — project context and hard conventions
---

This repo builds two Exordos MetaPaaS plugins: `victoriaaas` (VictoriaMetrics+VictoriaLogs storage) and `grafanaaas` (Grafana consumer), wired together by `observability.yaml.j2`.

**Before any non-trivial change, read `docs/DESIGN.md` in full** — it has the design rationale, exactly what's built vs. verified, and a risk list to check before extending. `AGENTS.md` has build/test/lint commands and coding conventions in more detail than fits here.

Hard rules, don't violate without updating `docs/DESIGN.md` too:

- `slug` (`victoria`/`grafana`) and `element_name` (`victoriaaas`/`grafanaaas`) are different things — the version catalog namespace is `$<element_name>.types.<slug>.versions`. Mixing them up is a documented, easy-to-reintroduce bug (see `exordos_metapaas/HOW_TO_BUILD_NEW_PAAS.md` Pitfall #9).
- Fixed UUIDs used verbatim everywhere in this repo's manifests — never invent new ones: metapaas project `4d657461-0000-0000-0000-000000000002`, owner role `726f6c65-0000-0000-0000-000000000002`, EM project id `12345678-c625-4fee-81d5-f691897b8142`.
- IAM permission names are `<policy_service_name>.<policy_name>.<action>` and must exactly match the controller's `__policy_service_name__`/`__policy_name__` class attrs — a mismatch fails silently as a 403 at runtime, not at import/build time.
- Any `dataplane/driver.py` code must follow the idempotency contract already used in `victoria/dataplane/driver.py` and `grafana/dataplane/driver.py`: `restore_from_dp()` reflects real on-disk state (never empty defaults — causes an infinite ~3s reload loop), `dump_to_dp()` only reloads/restarts when content actually changed (`_write_file_atomic` pattern).
- This repo builds on the `exordos_metapaas` framework — when unsure how something in that framework behaves, read the sibling repos on disk (`../exordos_metapaas`, `../exordos_s3`, `../exordos_db`), don't guess.
- This repo has been verified against a live Exordos Core + MetaPaaS stand (2026-08-17): VictoriaMetrics+VictoriaLogs+Grafana all ACTIVE, datasources and dashboards delivered. Most known risks in `docs/DESIGN.md` are now resolved. Remaining: functional tests need to be run against the stand (risk #7), dashboard caching is in-memory only (risk #8, low priority).

Commands: `tox -e py312` (unit tests), `tox -e ruff-check` (lint), `tox -e mypy` (types), `make build` (build DP images + manifests, needs the `exordos` CLI + packer + KVM). Functional tests need a live stand bootstrapped via `exordos_observability/victoria/tests/functional/prepare_env.py`.
