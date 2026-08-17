![Tests workflow](https://github.com/infraguys/exordos_observability/actions/workflows/tests.yaml/badge.svg)
![Build workflow](https://github.com/infraguys/exordos_observability/actions/workflows/build.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)

# Exordos Observability

Exordos Observability is the observability element for the [Exordos](https://github.com/infraguys/exordos_core) platform. It provides a VictoriaMetrics + VictoriaLogs metrics/logs backend and a Grafana consumer, packaged as [MetaPaaS](https://github.com/infraguys/exordos_metapaas) plugins and wired together by a composition manifest into one shared platform-wide observability cluster.

## Plugins

- **`victoriaaas`** (slug `victoria`) — VictoriaMetrics + VictoriaLogs storage building block
- **`grafanaaas`** (slug `grafana`) — Grafana consumer with declaratively provisioned datasources and dashboards

## Repository layout

```text
exordos_observability/
├── docs/                    # documentation (index, design, how-to)
├── exordos/
│   ├── exordos.yaml         # build config
│   ├── images/              # DP install/bootstrap scripts
│   └── manifests/           # element + composition manifests
├── etc/                     # DP config templates + systemd units
├── exordos_observability/   # Python package (victoria + grafana plugins)
├── pyproject.toml, tox.ini, Makefile
└── .github/workflows/
```

## Documentation

- [Exordos Observability Documentation](docs/index.md)
- [Design & Status](docs/DESIGN.md)
- [How-To Guide](docs/HOWTO.md)

## Related projects

- [Exordos Core](https://github.com/infraguys/exordos_core) — the platform
- [Exordos MetaPaaS](https://github.com/infraguys/exordos_metapaas) — PaaS framework and plugin contract
- [Exordos S3](https://github.com/infraguys/exordos_s3) — reference MetaPaaS plugin

## License

Apache License 2.0
