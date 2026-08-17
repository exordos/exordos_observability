# AGENTS.md

Context for any coding agent (Claude Code, OpenCode, Windsurf, Cursor, etc.) working in this repository.

## What this is

`exordos_observability` provides two MetaPaaS plugins for the Exordos platform: **`victoriaaas`** (VictoriaMetrics + VictoriaLogs storage, slug `victoria`) and **`grafanaaas`** (Grafana consumer, slug `grafana`). They're independent, composable building blocks — each runs on its own NodeSet and supports multiple independent instances — wired together by `observability.yaml.j2` into one shared platform-wide observability cluster.

**Read `docs/DESIGN.md` before making any non-trivial change.** It has the full context: why things are structured this way, exactly what's been built vs. verified, and a list of unverified assumptions to check before extending. For practical how-to guides (creating instances, adding datasources/dashboards, managing passwords), see [`docs/HOWTO.md`](docs/HOWTO.md). This file (AGENTS.md) only covers day-to-day conventions and commands.

This repo is a MetaPaaS plugin, built against the `exordos_metapaas` framework. Sibling repos on disk (`../exordos_metapaas`, `../exordos_s3`, `../exordos_db`, `../exordos_core`) are the source of truth for how that framework actually behaves — when in doubt about a framework contract, read the sibling repo's code, don't guess.

## Repo layout

```
exordos_observability/
├── docs/DESIGN.md                  # full design rationale, status, risks — READ FIRST
├── exordos/
│   ├── exordos.yaml                # build config
│   ├── images/*.sh                 # DP install/bootstrap shell scripts
│   └── manifests/*.yaml.j2         # element + composition manifests
├── etc/{exordos_metapaas, systemd}/  # DP config templates + systemd units
├── exordos_observability/
│   ├── victoria/                   # slug=victoria, element_name=victoriaaas
│   │   ├── definition.py           # PaaSDefinition — the plugin's entry point
│   │   ├── controlplane/{dm,api,infra,paas}/
│   │   ├── dataplane/driver.py     # runs ON the VM, not the CP
│   │   ├── migrations/
│   │   └── tests/{unit,functional}/
│   └── grafana/                    # same shape
├── pyproject.toml, tox.ini, Makefile
└── .github/workflows/
```

## Setup / build / test commands

This is a Python 3.10+ project using `uv` + `tox`. There is no local venv checked in.

```bash
# Install dev tooling
uv tool install tox --with tox-uv

# Unit tests (both plugins)
tox -e py312            # or: make test

# Lint
tox -e ruff-check       # or: make lint
tox -e ruff              # auto-fix + format, or: make format

# Type check
tox -e mypy              # or: make typecheck

# Functional tests — REQUIRES a live Exordos Core + MetaPaaS stand
tox -e py312-functional  # or: make functional
# Bootstrap one first via exordos_observability/victoria/tests/functional/prepare_env.py

# Build the element (DP images + manifests) — requires the `exordos` CLI + packer + KVM
make build REPOSITORY=<repo-url> INDEX_URL=<pip-index-url>
```

`tox.ini`'s `TEST_PATH` for unit tests is `exordos_observability/victoria/tests/unit exordos_observability/grafana/tests/unit` — both plugins' unit tests run together in one env. Functional tests are split analogously; the cross-plugin end-to-end suite currently lives under `exordos_observability/victoria/tests/functional/` (it needs both plugins installed, so it had to live in one place — see `docs/DESIGN.md` M7 note).

## Code conventions (do not deviate without a reason — these mirror `exordos_s3`, the reference plugin)

- **`element_name` vs `slug` must never be conflated.** `slug` is the short internal name (`victoria`, `grafana`); `element_name` is the registered manifest namespace (`victoriaaas`, `grafanaaas`). The version catalog lives at `$<element_name>.types.<slug>.versions` — getting this backwards is documented as a common bug in `exordos_metapaas/HOW_TO_BUILD_NEW_PAAS.md` (Pitfall #9).
- **Well-known fixed UUIDs**, used verbatim across every manifest in this repo — don't invent new ones: metapaas project `4d657461-0000-0000-0000-000000000002`, owner role `726f6c65-0000-0000-0000-000000000002`, EM project id (goes in every `versions.*.description`) `12345678-c625-4fee-81d5-f691897b8142`.
- **IAM permission naming**: `<policy_service_name>.<policy_name>.<action>`, e.g. `exordos_observability.victoria_instance.create`. Must exactly match the controller's `__policy_service_name__`/`__policy_name__` — a mismatch is a silent 403 at runtime, not an import-time error.
- **DP driver idempotency contract** (applies to any `dataplane/driver.py` code): `restore_from_dp()` must reflect the *real* on-disk/service state, never return empty defaults (causes an infinite reload loop every reconciliation tick, ~3s). `dump_to_dp()` must diff-before-write and only trigger a reload/restart when content actually changed. See `victoria/dataplane/driver.py` and `grafana/dataplane/driver.py` for the two implementations already following this (`_write_file_atomic` pattern).
- **Manifest grammar**: `$<element>.<category>.<name>:` to declare, `$path.$name:field` to reference a field (`:uuid`, `:value`, `:name`, chained like `:default_network:ipv4`), `f"...{$x:y}..."` for inline string interpolation. Full spec: `../exordos_core/docs/em/manifest.md`.
- License header: every `.py` and shell file in this repo starts with the Apache-2.0 / Genesis Corporation header block — copy it from an existing file, don't invent new wording.

## Testing instructions

- Always run `tox -e ruff-check` and `tox -e py312` before considering a change done.
- **Do NOT run `tox -e mypy`.** The user does not want mypy checks run; ignore the `make typecheck` / `tox -e mypy` commands listed above.
- Functional tests will `pytest.skip()` gracefully if no live stand is reachable — they are not meant to run in a plain CI checkout without `prepare_env.py` having been run first.
- New DP driver code must have a unit test for the `dump_to_dp`/`restore_from_dp` round-trip using `tmp_path` + `monkeypatch`, following the existing pattern in `*/tests/unit/test_driver.py` (copied from `metapaas_demo`'s own test style).

## PR / commit instructions

- Do not commit generated `__pycache__/`, `.tox/`, `output/`, or `dist/` — already covered by `.gitignore`.
- Keep commits scoped to one milestone/concern where practical; see `docs/DESIGN.md`'s milestone table for the natural chunks (M1–M7) this repo was originally built in.
