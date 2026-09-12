# Exordos Observability Element — Design & Status

This document is the permanent record of the design behind this repository: why it's structured the way it is, what's been built, what's still unverified, and what's explicitly out of scope. It is meant to let any agent or developer — regardless of tool — pick up this work with full context. For practical how-to guides (creating instances, adding datasources/dashboards, managing passwords), see [`HOWTO.md`](HOWTO.md). See also `AGENTS.md` (repo conventions, build/test commands) and `CLAUDE.md`/`.windsurf/rules/` (tool-specific pointers to this file).

## Context

`exordos_observability` is the observability element for the Exordos platform: a VictoriaMetrics + VictoriaLogs metrics/logs backend, consumed initially by Grafana (AlertManager planned later). Node-level agents (vmagent, vlagent, node_exporter) are baked into the Exordos base image and are out of scope for this repo — this repo is only the *server-side* stack plus the platform glue (manifests, IAM, control-plane code) that lets it be declared and reconciled through Exordos Core.

This was genuinely greenfield work: no existing element, doc, or code anywhere in the Exordos platform mentioned VictoriaMetrics, Grafana, or observability before this repo existed. The architectural precedent followed is the **metapaas plugin contract** (`exordos_metapaas`), which that platform's own `DESIGN.md` recommends over building a standalone control-plane element (the older pattern used by `exordos_db`, which duplicates ~70% boilerplate per plugin).

## Binding architecture decisions

These were made deliberately during scoping and should not be re-litigated without a good reason:

1. **Built as metapaas plugin(s)**, not a standalone element — reuses the shared metapaas control plane, IAM, and reconciliation machinery.
2. **"Storages" (VictoriaMetrics+VictoriaLogs) and "consumers" (Grafana, later AlertManager) are independent, composable building blocks** — each is its own plugin type running in its own NodeSet, each independently multi-instantiable (so several fully independent observability clusters can coexist in one Exordos installation, the same way `exordos_s3` supports multiple named `S3Instance`s). A higher-level manifest — `observability.yaml.j2` — composes one instance of each block together for platform-wide shared use, mirroring `communal_s3.yaml.j2` / `communal_pg_cluster.yaml.j2` in the sibling repos.
3. **Real separate virtual disks** for metrics and logs (plus root), not one shared data disk with subdirectories.
4. **Phase 1 (this repo's current state) is a full vertical slice**: VictoriaMetrics + VictoriaLogs in single-node mode, wired to a Grafana instance with working datasources and declarative dashboards. AlertManager is explicitly deferred. HA/distributed mode for Victoria is future work, but the data model reserves room for it (mirrors `S3InstanceKind.SINGLE_NODE` / a future `DISTRIBUTED`).

Reference repos this was built against (read these before making non-trivial changes — they're the source of truth for the framework, not this doc): `../exordos_metapaas` (framework + `HOW_TO_BUILD_NEW_PAAS.md`), `../exordos_s3` (best full-featured reference plugin — nearly every pattern here was adapted from it), `../exordos_db` (legacy standalone-element pattern, and the clearest example of the "communal" manifest convention), `../exordos_core/docs` (platform manifest grammar, `em/manifest.md`).

## Implementation status

**Verified against a live Exordos Core + MetaPaaS stand (2026-08-17).** VictoriaMetrics + VictoriaLogs + Grafana all ACTIVE, datasources and dashboards delivered end-to-end. The deployment was verified on a stand with core at `10.20.0.2`, metapaas-cp at `10.20.0.20`, grafana DP at `10.20.0.21`, victoria DP at `10.20.0.22`. Functional tests (`tests/functional/`) still need to be run against the stand to cover the full E2E suite — see **Known risks / unverified assumptions** below for remaining items.

### Repo layout

```
exordos_observability/
├── docs/DESIGN.md                    # this file
├── AGENTS.md, CLAUDE.md, .windsurf/rules/   # cross-agent context (see below)
├── exordos/
│   ├── exordos.yaml                  # build config: 4 manifest elements, 2 DP images
│   ├── images/{victoria,grafana}_dp_{install,bootstrap}.sh
│   └── manifests/
│       ├── victoriaaas.yaml.j2       # registers victoria plugin type + IAM + version catalog
│       ├── grafanaaas.yaml.j2        # same, for grafana
│       ├── observability.yaml.j2   # 1 victoria instance + 1 grafana instance + datasources
│       └── example_observability.yaml.j2    # functional-test fixture manifest
├── etc/{exordos_metapaas/*.conf+logging.yaml, systemd/*.service}
├── exordos_observability/
│   ├── victoria/   # slug=victoria, element_name=victoriaaas
│   │   ├── definition.py, constants.py
│   │   ├── controlplane/{dm, api, infra/{dm,services}, paas/{dm,services}}
│   │   ├── dataplane/driver.py
│   │   ├── migrations/0000-init-victoria.py
│   │   └── tests/{unit, functional}   # functional/ holds the cross-plugin E2E suite (see below)
│   └── grafana/    # slug=grafana, element_name=grafanaaas — same shape
├── pyproject.toml, tox.ini, Makefile
└── .github/workflows/{tests.yaml, build.yml}
```

One Python distribution (`exordos_observability`), two `PaaSDefinition`s registered as two `exordos_metapaas_paas` entry points — confirmed workable against `exordos_metapaas/exordos_metapaas/registry.py`'s `discover_paas()`, which just iterates all entry points in that group and keys by `slug`.

**Naming, verified against `s3aas.yaml.j2`**: `slug="victoria"` / `element_name="victoriaaas"`, `slug="grafana"` / `element_name="grafanaaas"`. The version catalog namespace is `$<element_name>.types.<slug>.versions` (e.g. `$victoriaaas.types.victoria.versions`) — `element_name` and `slug` must never be conflated (this is documented as `HOW_TO_BUILD_NEW_PAAS.md` Pitfall #9, and it's an easy mistake to reintroduce when adding a third plugin later).

### Victoria (storages) plugin — what was built

- **CP `VictoriaInstance` model** (`controlplane/dm/models.py`): `name`, `project_id`, `cpu`, `ram`, `metrics_disk_size`, `logs_disk_size`, `retention_period`, `replicas` (locked to 1 via `_validate_kind`), `version` relationship, and two read-only fields populated by the infra builder: `metrics_endpoint` / `logs_endpoint` (plain strings like `"http://<ip>:8428"`). These two scalar fields are the mechanism that lets Grafana's datasource `url:` reference them directly with ordinary `$path:field` manifest syntax — deliberately chosen over exposing `ipsv4` (a list) because no manifest pattern anywhere in the platform dereferences a list field directly.
- **Infra layer** (`controlplane/infra/dm/models.py` + `infra/services/builder.py`): one `NodeSet` with a 3-entry `SetDisksSpec` — `root` (image), `metrics` (label + mount_point `/var/lib/victoria-metrics-data`), `logs` (label + mount_point `/var/lib/victoria-logs-data`). `CoreInfraBuilder` keeps disk/cpu/ram/replicas in sync, syncs node keys, and delivers one static `Config` (`/etc/exordos_metapaas/victoria.env`: ports, retention) with `OnChangeShell` restarting both `victoriametrics`/`victorialogs` systemd units.
- **Paas layer** (`controlplane/paas/dm/models.py` + `paas/services/builder.py`): a minimal `VictoriaInstanceNode` derivative resource carrying only node identity (`name`). Victoria has no dynamic child resources (no buckets/users equivalent) — its real config is delivered entirely via the generic `Config` resource above, not through this derivative. **This paas layer exists purely because every other plugin in the platform (including the minimal reference plugin, `metapaas_demo`) has at least one derivative resource + capability driver, and none has ever shipped without one** — so this mirrors that proven-minimal shape rather than attempting an unprecedented driver-less plugin.
- **DP driver** (`dataplane/driver.py`): `VictoriaCapabilityDriver` / `VictoriaInstance(meta.MetaDataPlaneModel)` — writes/reads a tiny identity marker file (`/var/lib/exordos/exordos_metapaas/victoria_node.env`), mirroring `metapaas_demo`'s `DemoInstance`/`DemoCapabilityDriver` almost line-for-line.
- **New engineering — multi-disk bootstrap** (`exordos/images/victoria_dp_bootstrap.sh`): `lib_bootstrap.sh`'s `find_persistent_disk()` (used unmodified everywhere else — `exordos_s3`, `exordos_db`, `exordos_metapaas`) only returns the first non-root disk. Victoria needs two. The bootstrap script enumerates non-root whole-disk block devices via `lsblk`, sorted by device name, and treats the first as `metrics`, second as `logs` — **positional, since no in-guest disk-label lookup exists anywhere in the platform**. Reuses `prepare_persistent_disk()` and `migrate_to_persistent_restart()` (for `/var/log`) unchanged. Fails loudly if fewer than 2 extra disks are attached.
- **DP install** (`exordos/images/victoria_dp_install.sh`): installs VictoriaMetrics, VictoriaLogs, and vmauth (part of the vmutils package) from upstream GitHub releases at **pinned versions** (`VM_VERSION`/`VL_VERSION` variables at the top of the script). Earlier versions of this doc described GitHub-API latest-resolution by pattern-matching asset names; this was changed to pinned versions for reproducibility.

### Grafana (consumer) plugin — what was built

- **CP `GrafanaInstance` + `GrafanaDatasource` models** (`controlplane/dm/models.py`): instance has `name`, `project_id`, `cpu`, `ram`, `root_disk_size`, `auth` (polymorphic kind model — `PasswordAuth` with `password`, or `OidcAuth` with OIDC endpoints; `HIDDEN` in API responses, sourced from `$core.secret.passwords` via manifest), `replicas` (locked to 1 via the type constraint and the migration's `CHECK (replicas = 1)` — single-node only for now, the field is exposed so consumer manifests have a stable interface), `ui_url` (RO). For `PasswordAuth`, the password is declared as a Secret Manager resource (`AUTO_URL_SAFE`) in `observability.yaml.j2` and referenced via `$core.secret.passwords.$grafana_admin_password:value`; the infra builder renders it into the Grafana env file as `GF_SECURITY_ADMIN_PASSWORD`. For `OidcAuth`, the builder renders `GF_AUTH_GENERIC_OAUTH_*` env vars (see [`docs/DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md)). When the config changes, the platform agent re-delivers the env file and Grafana restarts via `OnChangeShell`. `GrafanaDatasource` is a child resource (`InstanceChildModel`/`_touch_parent` pattern copied from `S3Bucket`/`S3Policy`) with `name`, `type` (`prometheus`|`victoriametrics-logs-datasource`), `url` (caller-supplied — this is the cross-plugin wiring point), `is_default`, and an optional `auth` field (`BasicAuth` kind model with `username`/`password` — used for vmauth-protected Victoria endpoints; see [`docs/DESIGN-victoria-auth.md`](DESIGN-victoria-auth.md)). The VictoriaLogs datasource type uses the native `victoriametrics-logs-datasource` Grafana plugin (installed in the DP image) rather than the Loki type, because VictoriaLogs does not expose a Loki-compatible API.
- **CP `GrafanaArtifactDashboard` model** (`controlplane/dm/models.py`): top-level artifact storing a Grafana dashboard definition, independent of any Grafana instance. The dashboard content is fetched via a polymorphic `source` field (`AbstractDashboardSource` kind model). The computed `version_ref` URN encodes the dashboard uuid and its source digest (`{uuid}_{source_digest}`), so consumer resources can reference a specific dashboard version via `$grafanaaas.types.grafana.dashboards.$x:version_ref`. Three source kinds are implemented (`controlplane/dm/sources.py`):
  - `UrnDashboardSource` (`kind: urn`) — resolves a `urn:artifacts:<uuid>` reference against Exordos Core's `RepoArtifact` registry (via the builder's authenticated core client) to get a trusted `uri`, then fetches JSON from that `uri` via `bazooka`; digest is the URN itself. This replaced an earlier `UrlDashboardSource` that fetched an arbitrary caller-supplied HTTP URL directly — an SSRF risk, since the control plane would dereference any address a project-scoped user supplied. The URN form only ever resolves to an artifact a trusted `Repository` has already indexed, so the CP never makes an outbound request driven directly by request input.
  - `RawDashboardSource` (`kind: raw`) — stores inline JSON; digest is SHA-256 of the stable JSON serialization.
  - `BundledDashboardSource` (`kind: bundled`) — downloads Grafana catalog revisions from `https://grafana.com/api/dashboards/{id}/revisions/{rev}/download`; digest is `grafana_{id}_{rev}`.
  - All sources cache their `dashboard()` result in memory after the first successful fetch (`AbstractDashboardSource._cached_dashboard`), so repeated builder iterations don't re-fetch from external providers and are resilient to transient rate-limiting (HTTP 429). The cache is per-instance, not persisted.
- **CP `GrafanaDashboard` child model** (`controlplane/dm/models.py`): dashboard binding on a Grafana instance, referencing a `GrafanaArtifactDashboard` via `version_ref`. The paas builder resolves the artifact UUID from the `version_ref` (part before the first underscore), fetches the dashboard content from the artifact's source, and stores it in the child's `content` field with `saved_version_ref` updated to match. `needs_content_refresh()` returns True when `version_ref` has changed since the last fetch. Both `content` and `saved_version_ref` are `HIDDEN` in the API (builder-managed fields).
- **Infra layer**: single `NodeSet`, root disk only.
- **Paas layer + DP driver**: `GrafanaInstanceNode` derivative carries a `datasources` dict (assembled from `GrafanaDatasource` child rows) and a `dashboards` dict (assembled from `GrafanaDashboard` child rows with resolved content). The DP driver (`dataplane/driver.py`) renders datasources into Grafana's native **file-based provisioning** format (`/etc/grafana/provisioning/datasources/exordos.yaml`) and dashboards into individual JSON files under `/var/lib/grafana/dashboards/exordos/` (one subdirectory per Grafana folder) plus a dashboard provisioning YAML (`/etc/grafana/provisioning/dashboards/exordos.yaml`). Reload via Grafana's HTTP admin API (`POST /api/admin/provisioning/{datasources,dashboards}/reload`) only when the rendered content actually changed — Grafana 13+ does not re-provision on SIGHUP. Stale dashboard files are removed; `restore_from_dp()` verifies on-disk file presence and drops missing entries from the dashboards dict.
- **Readiness gating**: the PaaS `GrafanaInstance` (and `VictoriaInstance`) inherit `DependenciesActiveReadinessMixin`, depending on their IaaS instance (`grafana_instance_iaas` / `victoria_instance_iaas`) being `ACTIVE`. This prevents the PaaS builder from persisting the derivative (`ua_target_resources`) before the dataplane agent has registered in `ua_agents`, which would cause a foreign-key violation and roll back the entire transaction (including any dashboard content fetched in the same iteration).
- **Install** (`exordos/images/grafana_dp_install.sh`): installs Grafana from the official upstream APT repo, then masks its default `grafana-server.service` unit and runs it under a custom `exordos-metapaas-grafana.service` gated on the control-plane-delivered env file, matching this repo's `ConditionPathExists=` convention used by the Victoria units.

### Shared Grafana instance model — cross-project dashboards by design

The Grafana instance is a **shared** platform resource: multiple projects add their own dashboards (and datasources) to a single Grafana instance rather than each project spinning up its own Grafana. This is the intended usage model — a shared observability UI for the whole platform.

Consequences that follow from this decision and are **by design, not bugs**:

- `GrafanaDatasource` and `GrafanaDashboard` are children of a `GrafanaInstance` but are **not** required to share the instance's `project_id`. A dashboard from project A can be bound to a Grafana instance owned by project B. The platform's EM (Exordos Manager) policy layer is the gatekeeper: if EM allowed the request, the binding is valid.
- Artifact resolution (`GrafanaArtifactDashboard` via `version_ref`) looks up by UUID only, without filtering by `project_id`. This is intentional: the dashboard content is not project-sensitive — it is a JSON document with panels and queries. If a user has permission to create a dashboard binding (EM allowed it), they can reference any artifact.
- Datasource-to-dashboard access control is **not** enforced at the model level. If a dashboard is installed on a Grafana instance, it can query any datasource provisioned on that same instance. The assumption is: if EM allowed the dashboard to be installed, the user has access to all datasources on that Grafana. Fine-grained per-dashboard datasource ACLs are a future concern, not a Phase 1 requirement.

This means the `GrafanaInstance` is closer to a "community board" than a per-project sandbox. Projects that need isolation should deploy their own Grafana instance.

### `observability.yaml.j2` / `example_observability.yaml.j2`

Plain manifests (no plugin needed) that each create one Victoria instance + one Grafana instance + two datasources (prometheus + victoriametrics-logs-datasource type) **in the same manifest**, so the datasource `url:` fields reference the Victoria instance's `metrics_endpoint`/`logs_endpoint` via ordinary intra-manifest `$path:field` syntax — no `imports:` needed since everything is declared together. `example_observability.yaml.j2` is the smaller/cheaper twin used as the functional-test fixture.

`observability.yaml.j2` additionally ships a default **Node Exporter Full** dashboard (Grafana catalog ID 1860, revision 31) via a `BundledDashboardSource` artifact (`node_exporter_full`) and a child dashboard binding (`node_exporter`) on the Grafana instance, referencing the artifact's `version_ref` via intra-manifest `$path:field` syntax.

A separate, **proven-but-not-used** pattern exists for third-party manifests that want to attach resources to an instance owned by a *different* manifest (confirmed via `exordos/exordos/templates/platformizers/manifests/pgsql_communal/genesis/manifests/{{ project_name }}.yaml.j2` in the `exordos` CLI repo) — not needed for Phase 1, noted here for whoever builds self-service consumers of `observability` later.

### IAM

Both `victoriaaas.yaml.j2` and `grafanaaas.yaml.j2` follow `s3aas.yaml.j2`'s skeleton exactly: `$metapaas.types.<slug>` registration, one `$core.iam.permissions`+`$core.iam.permissionbinding` pair per action (`<slug>_instance.{create,read,update,delete}`, plus `datasource.*`, `grafana_dashboard.*`, and `grafana_artifact_dashboard.*` for grafana) bound to the fixed owner role `726f6c65-0000-0000-0000-000000000002` in the fixed metapaas project `4d657461-0000-0000-0000-000000000002`. `__policy_service_name__ = "exordos_observability"` for both plugins' controllers (shared across the repo — no collision since `__policy_name__` differs per resource type: `victoria_instance`, `grafana_instance`, `datasource`, `grafana_dashboard`, `grafana_artifact_dashboard`, etc.).

## Milestones (all scaffolded / code-complete)

| # | Milestone | Status |
|---|---|---|
| M1 | Repo scaffold: `pyproject.toml` (2 entry points), `tox.ini`, `Makefile`, `exordos/exordos.yaml`, CI skeleton, both `definition.py` | Done |
| M2 | Victoria CP + infra: models, migrations, controllers/routes, 3-disk `CoreInfraBuilder`, endpoint population | Done |
| M3 | Victoria DP: install script (pinned binary versions), multi-disk bootstrap, systemd units, minimal paas layer + driver | Done, verified on live stand |
| M4 | Grafana CP + infra + DP: instance/datasource models, single-disk infra, provisioning-YAML driver | Done |
| M5 | Datasource wiring in `observability.yaml.j2` (intra-manifest field refs) | Done |
| M6 | `observability.yaml.j2` + `example_observability.yaml.j2` finalized | Done |
| M7 | Unit tests (both plugins) + functional test suite (`conftest.py`, `prepare_env.py`, `test_observability_e2e.py`) | Done, never executed against a live stand |
| M8 | Declarative Grafana dashboards: `GrafanaArtifactDashboard` + polymorphic sources (`Urn`/`Raw`/`Bundled`), `GrafanaDashboard` child with `version_ref` resolution, DP dashboard provisioning (JSON files + YAML), `ReadinessMixin` on PaaS instances, Node Exporter dashboard in observability manifest | Done, verified against a live stand (datasources + dashboard delivery confirmed after fixing FK race + 429 rate-limit) |
| M9 | Victoria read-path auth: `vmauth` reverse proxy with read-only path ACL, `grafana-reader` Basic Auth user, datasource auth fields in `GrafanaDatasource` | Done, verified on live stand |
| M10 | Grafana OIDC authentication via Exordos IAM: `auth` kind model (`PasswordAuth`/`OidcAuth`), `GF_AUTH_GENERIC_OAUTH_*` env rendering, IAM client + Idp in manifest | Done |

## Explicitly out of scope for Phase 1

AlertManager plugin, VictoriaMetrics/VictoriaLogs HA/distributed mode, cross-element third-party consumer imports (pattern proven in `exordos` CLI's platformizer templates, not built here).

## Known risks / unverified assumptions — read before extending

A live deployment of `observability` has been verified against a real Exordos Core + MetaPaaS stand (2026-08-17). Datasource delivery was verified end-to-end (VictoriaMetrics + VictoriaLogs datasources appeared in Grafana with correct URLs). Dashboard delivery was verified after fixing two issues discovered during the live deployment (see M8 status). Previously listed risks (multi-disk bootstrap, positional disk ordering, Victoria DP driver, Grafana auth delivery, VictoriaLogs datasource type, `ReadinessMixin` behavior) have all been verified on the live stand and are no longer listed. Remaining items:

1. **Functional test suite reuses the composition instances — does not boot its own DP VMs.** `exordos_observability/victoria/tests/functional/` requires a live Core + MetaPaaS stand with the `observability` composition manifest already deployed (`prepare_env.py` bootstraps and deploys it). Rather than creating its own Victoria/Grafana instances (which would require an auth password for Grafana that the test doesn't have, and boot extra DP VMs that CI runners can't reliably accommodate), the suite reuses the composition's `victoria` / `grafana` instances (named via `EXORDOS_VICTORIA_INSTANCE_NAME` / `EXORDOS_GRAFANA_INSTANCE_NAME`, defaulting to `victoria` / `grafana`) and the datasources the composition already wired. The `observability_api_client` fixture uses an **unscoped admin token** so it can read those instances regardless of project: the composition creates them in the EM project (`12345678-c625-4fee-81d5-f691897b8142`), and `PolicyBasedController._force_project_id` rejects cross-project reads for project-scoped tokens — an unscoped `*.*.*` admin token bypasses that check. Required env vars: `EXORDOS_ENDPOINT=http://10.20.0.2/api/core`, `EXORDOS_OBSERVABILITY_CP_URL=http://10.20.0.20:8080`, `EXORDOS_USERNAME=admin`, `EXORDOS_PASSWORD=<admin password>`, `EXORDOS_POLL_TIMEOUT=600`. The tests can't verify Grafana query results directly (auth credentials are `HIDDEN` in the API), so they check datasource wiring + direct VictoriaMetrics/VictoriaLogs query results only.
2. **Dashboard content caching is in-memory only — low priority.** `AbstractDashboardSource._cached_dashboard` caches the fetched dashboard JSON on the source object for the lifetime of that Python object. If the builder process restarts, the cache is lost and the next iteration re-fetches from the external provider. This is acceptable for the current single-instance observability deployment, but if many dashboards are provisioned, a persistent cache (e.g. on the artifact model) may be needed to avoid repeated external fetches after CP restarts.

## Continuing this work

1. The observability deployment has been verified against a live stand (datasources confirmed, dashboards confirmed after fixes, multi-disk bootstrap confirmed, Victoria DP driver confirmed, pinned binary versions confirmed, vmauth read-path auth confirmed). Functional tests (`tests/functional/`) reuse the composition instances (see risk #1) and need a live stand with the `observability` composition deployed to run.
2. Remaining items: dashboard caching (#2, low priority).
3. Future candidates (not started, no code): AlertManager plugin (third building block, same pattern as Grafana), VictoriaMetrics/VictoriaLogs HA (`kind: distributed`, multiple `NodeSet`s per instance — no existing plugin does this yet, will need new design work). vmauth JWT/IAM integration (Variant I/III in [`docs/DESIGN-victoria-auth.md`](DESIGN-victoria-auth.md)) — currently Basic Auth is used.
