# Exordos Observability

Exordos Observability is the observability element for the
[Exordos](https://github.com/infraguys/exordos_core) platform. It provides
a metrics and logs backend (VictoriaMetrics + VictoriaLogs) and a
visualization layer (Grafana), packaged as
[MetaPaaS](https://github.com/infraguys/exordos_metapaas) plugins and
wired together by a composition manifest into one shared platform-wide
observability cluster.

## What it does

- **Metrics storage** — VictoriaMetrics collects and stores Prometheus-
  compatible metrics with configurable retention.
- **Logs storage** — VictoriaLogs collects and stores structured logs
  with a native query language.
- **Visualization** — Grafana provides dashboards and alerting, with
  datasources and dashboards provisioned declaratively through manifests.

Node-level agents (vmagent, vlagent, node_exporter) that scrape and ship
data *into* this stack are baked into the Exordos base image and are out
of scope for this repository.

## Architecture

Exordos Observability consists of two independent, composable MetaPaaS
plugins, each running on its own NodeSet:

- **`victoriaaas`** (slug `victoria`) — the storages building block. Each
  `VictoriaInstance` runs VictoriaMetrics and VictoriaLogs in single-node
  mode on a three-disk node (root, metrics data, logs data). The instance
  exposes `metrics_endpoint` and `logs_endpoint` scalar fields that
  consumer manifests reference directly.

- **`grafanaaas`** (slug `grafana`) — the consumer building block. Each
  `GrafanaInstance` runs Grafana on a single-disk node with declaratively
  provisioned datasources (`GrafanaDatasource` child resources) and
  dashboards (`GrafanaDashboard` child resources bound to
  `GrafanaArtifactDashboard` artifacts with polymorphic content sources).

A composition manifest (`observability.yaml.j2`) wires one Victoria
instance and one Grafana instance together with Prometheus and
VictoriaLogs datasources, plus a default Node Exporter Full dashboard.
This mirrors the "communal" pattern used by other Exordos elements
(e.g. `exordos_s3`'s `communal_s3.yaml.j2`).

Both plugins support multiple independent instances per project, so
several fully independent observability clusters can coexist in one
Exordos installation.

## Project structure

```text
exordos_observability/
├── docs/                        # this documentation
│   ├── index.md                 # overview (this file)
│   ├── DESIGN.md                # design rationale, status, known risks
│   ├── HOWTO.md                 # practical how-to guides
│   └── DESIGN-grafana-oidc.md   # OIDC authentication design (not yet implemented)
├── exordos/
│   ├── exordos.yaml             # build config: 4 manifest elements, 2 DP images
│   ├── images/                  # DP install/bootstrap scripts per plugin
│   └── manifests/               # element + composition manifests
│       ├── victoriaaas.yaml.j2  # victoria plugin registration + IAM + versions
│       ├── grafanaaas.yaml.j2   # grafana plugin registration + IAM + versions
│       ├── observability.yaml.j2        # composition: 1 victoria + 1 grafana + datasources + dashboard
│       └── example_observability.yaml.j2 # smaller fixture for functional tests
├── etc/                         # DP config templates + systemd units
├── exordos_observability/       # Python package (one distribution, two plugins)
│   ├── victoria/                # slug=victoria, element_name=victoriaaas
│   │   ├── definition.py        # PaaSDefinition entry point
│   │   ├── controlplane/        # CP models, API, infra/paas builders
│   │   ├── dataplane/           # DP driver (runs on the VM)
│   │   ├── migrations/          # DB migrations
│   │   └── tests/               # unit + functional tests
│   └── grafana/                 # slug=grafana, element_name=grafanaaas
│       └── ... (same shape)
├── pyproject.toml, tox.ini, Makefile
└── .github/workflows/           # CI: tests + build
```

Each plugin subpackage follows the standard MetaPaaS plugin shape
(`controlplane/{dm,api,infra,paas}`, `dataplane/driver.py`, `migrations/`)
documented in
[`exordos_metapaas/HOW_TO_BUILD_NEW_PAAS.md`](https://github.com/infraguys/exordos_metapaas/blob/main/HOW_TO_BUILD_NEW_PAAS.md).

## Related projects

- [Exordos Core](https://github.com/infraguys/exordos_core) — the
  platform. Provides the control plane, IAM, manifest engine, compute
  management, and the element ecosystem.
- [Exordos MetaPaaS](https://github.com/infraguys/exordos_metapaas) — the
  PaaS framework. Defines the plugin contract (`PaaSDefinition`),
  reconciliation machinery, and the universal agent runtime. This
  repository's plugins are built against it.
- [Exordos S3](https://github.com/infraguys/exordos_s3) — the reference
  MetaPaaS plugin. Nearly every pattern in this repository was adapted
  from it.

## Documentation

- [Design & Status](DESIGN.md) — architecture decisions, what's been
  built, implementation status, and known risks.
- [How-To Guide](HOWTO.md) — practical guides for creating instances,
  adding datasources and dashboards, and wiring Victoria + Grafana
  together.
- [Grafana OIDC Design](DESIGN-grafana-oidc.md) — design for OIDC
  authentication via Exordos IAM (not yet implemented).
