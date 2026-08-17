# How to Use the Exordos Observability Element

This guide covers common operations: creating Victoria/Grafana instances,
configuring Grafana authentication (password or OIDC), adding datasources
and dashboards, and the available dashboard source kinds. All examples use
manifest declarations — the same approach used by `observability.yaml.j2`
and `example_observability.yaml.j2`.

For the full design rationale and architecture, see
[`DESIGN.md`](DESIGN.md). For build/test/lint commands and coding
conventions, see [`AGENTS.md`](AGENTS.md).

## Table of contents

- [Prerequisites](#prerequisites)
- [How to create a Victoria instance](#how-to-create-a-victoria-instance)
- [How to create a Grafana instance](#how-to-create-a-grafana-instance)
- [Grafana authentication methods](#grafana-authentication-methods)
- [How to add a datasource to Grafana](#how-to-add-a-datasource-to-grafana)
- [How to add a dashboard to Grafana](#how-to-add-a-dashboard-to-grafana)
- [Dashboard source kinds](#dashboard-source-kinds)
- [How to wire Victoria + Grafana together](#how-to-wire-victoria--grafana-together)
- [How to reference resources from another manifest](#how-to-reference-resources-from-another-manifest)

---

## Prerequisites

The `victoriaaas` and `grafanaaas` elements must be installed in the
Exordos Core installation. Both are registered as MetaPaaS plugins and
expose their resource collections under:

- `$victoriaaas.types.victoria.instances` — Victoria instances
- `$victoriaaas.types.victoria.versions` — Victoria DP image catalog
- `$grafanaaas.types.grafana.instances` — Grafana instances
- `$grafanaaas.types.grafana.versions` — Grafana DP image catalog
- `$grafanaaas.types.grafana.dashboards` — Dashboard artifacts (top-level)

Every manifest that creates instances needs to import a version from the
plugin's version catalog. See the `imports:` section in any example below.

**Fixed UUIDs used across all manifests in this repo:**

| UUID | Meaning |
| ------ | --------- |
| `4d657461-0000-0000-0000-000000000002` | MetaPaaS project (IAM permissions live here) |
| `726f6c65-0000-0000-0000-000000000002` | Owner role (binds permissions to users) |
| `12345678-c625-4fee-81d5-f691897b8142` | EM project id (goes in `project_id` fields and version `description`) |

---

## How to create a Victoria instance

A Victoria instance runs VictoriaMetrics (metrics, port 8428) and
VictoriaLogs (logs, port 9428) on a single node with three disks: root,
metrics data, and logs data. VictoriaMetrics/VictoriaLogs bind to
`127.0.0.1`, so they are only reachable through **vmauth** — a reverse
proxy on the same node that enforces read authentication and leaves the
write path open for agents. The `vmauth` field is **required** and
configures that proxy.

```yaml
resources:
  # vmauth Basic Auth password for the read-only grafana-reader user.
  # Referenced by both the Victoria instance (delivered into vmauth.yaml)
  # and any Grafana datasource that reads from this Victoria instance.
  $core.secret.passwords:
    vmauth_reader_password:
      name: "vmauth_reader_password"
      description: "vmauth Basic Auth password for Grafana reader"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      method: "AUTO_URL_SAFE"
      constructor:
        kind: plain
      default_length: 32

  $victoriaaas.types.victoria.instances:
    my_victoria:
      name: "my-victoria"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      cpu: 2
      ram: 2048
      metrics_disk_size: 20        # GB
      logs_disk_size: 20           # GB
      retention_period: "30d"      # e.g. "7d", "30d", "90d"
      replicas: 1                  # Phase 1: single-node only
      vmauth:
        kind: basic
        username: "grafana-reader"
        password: $core.secret.passwords.$vmauth_reader_password:value
      version_ref: $my_manifest.imports.$victoria_v1:version_ref

imports:
  victoria_v1:
    element: "$victoriaaas"
    kind: "resource"
    link: "$victoriaaas.types.victoria.versions.$v1"
```

**Fields:**

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `name` | string | yes | Instance name (1–255 chars) |
| `project_id` | UUID | yes | EM project id |
| `cpu` | int | yes | 1–128 cores |
| `ram` | int | yes | 512–1073741824 MB |
| `metrics_disk_size` | int | yes | 8–1073741824 GB (shrink not supported) |
| `logs_disk_size` | int | yes | 8–1073741824 GB (shrink not supported) |
| `retention_period` | string | no | Default: `30d` |
| `replicas` | int | no | Locked to 1 (single-node mode) |
| `vmauth` | kind model | yes | vmauth reader auth (see below) |
| `version_ref` | string | yes | From the version catalog import |

**`vmauth` auth kinds** (polymorphic, selected by `kind`):

| Kind | Fields | Description |
| ------ | -------- | ------------- |
| `basic` | `username` (1–128), `password` (1–256) | HTTP Basic Auth rendered into `vmauth.yaml`. Grafana datasources reference the same pair to authenticate read requests. |

**Read-only fields** (populated by the infra builder):

| Field | Description |
| ------- | ------------- |
| `status` | `NEW` → `IN_PROGRESS` → `ACTIVE` |
| `ipsv4` | List of node IP addresses |
| `metrics_endpoint` | `http://<ip>:8428` — vmauth proxy, use as Grafana datasource URL |
| `logs_endpoint` | `http://<ip>:9428` — vmauth proxy, use as Grafana datasource URL |

> **Note:** `metrics_endpoint` and `logs_endpoint` point at vmauth (same
> ports), which proxies read requests to localhost VM/VL and enforces
> Basic Auth. They are plain scalar strings (not list fields), so they
> can be referenced directly in manifests with `$path:field` syntax. This
> is deliberate — no manifest pattern in the platform dereferences a list
> field directly.

---

## How to create a Grafana instance

A Grafana instance runs Grafana on a single node with one root disk.
Grafana authentication is configured via the polymorphic `auth` field
(see [Grafana authentication methods](#grafana-authentication-methods)
below). The simplest option is a built-in admin password.

```yaml
resources:
  # The admin password must be sourced from the Core Secret Manager.
  $core.secret.passwords:
    my_grafana_password:
      name: "my_grafana_password"
      description: "Grafana admin password"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      method: "AUTO_URL_SAFE"
      constructor:
        kind: plain
      default_length: 32

  $grafanaaas.types.grafana.instances:
    my_grafana:
      name: "my-grafana"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      cpu: 2
      ram: 2048
      root_disk_size: 20           # GB
      replicas: 1                  # Phase 1: single-node only
      auth:
        kind: password
        password: $core.secret.passwords.$my_grafana_password:value
      version_ref: $my_manifest.imports.$grafana_v13:version_ref

imports:
  grafana_v13:
    element: "$grafanaaas"
    kind: "resource"
    link: "$grafanaaas.types.grafana.versions.$v13"
```

**Fields:**

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `name` | string | yes | Instance name (1–255 chars) |
| `project_id` | UUID | yes | EM project id |
| `cpu` | int | yes | 1–128 cores |
| `ram` | int | yes | 512–1073741824 MB |
| `root_disk_size` | int | yes | 8–1073741824 GB |
| `replicas` | int | no | Locked to 1 (single-node mode) |
| `auth` | kind model | yes | Grafana auth (password or oidc — see below) |
| `version_ref` | string | yes | From the version catalog import |

**Read-only fields:**

| Field | Description |
| ------- | ------------- |
| `status` | `NEW` → `IN_PROGRESS` → `ACTIVE` |
| `ipsv4` | List of node IP addresses |
| `ui_url` | Grafana UI URL |

> **Note:** `auth` is `HIDDEN` in API responses — no client, including
> the functional test suite, can retrieve the password or OIDC client
> secret after creation.

---

## Grafana authentication methods

The `auth` field on a Grafana instance is a polymorphic kind model with
two implementations (`controlplane/dm/auth.py`):

### `password` — built-in admin password

Renders `GF_SECURITY_ADMIN_PASSWORD` from the `password` field. Use this
for standalone deployments without a central identity provider.

```yaml
auth:
  kind: password
  password: $core.secret.passwords.$my_grafana_password:value
```

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `password` | string | yes | 1–256 chars, sourced from `$core.secret.passwords` |

### `oidc` — generic OAuth/OIDC provider

Renders the `GF_AUTH_GENERIC_OAUTH_*` environment variables so users
authenticate through a central identity provider (e.g. Core IAM). The
admin password is **not** delivered by the control plane — it is derived
deterministically from the OIDC `client_secret` (a SHA-256 hash, private,
not exposed via API) so it is stable across reconciliation ticks but not
predictable from public instance data. Nobody is expected to use it for
login; all users authenticate via OIDC.

```yaml
auth:
  kind: oidc
  client_id: $core.iam.clients.$grafana_oidc:client_id
  client_secret: $core.secret.passwords.$grafana_oidc_client_secret:value
  auth_url: f"{$core.vs.variables.$var_core_api_url:value}/api/core/v1/iam/idp/{$core.iam.idp.$grafana_idp:uuid}/actions/authorize/invoke"
  token_url: f"{$core.vs.variables.$var_core_api_url:value}/api/core/v1/iam/clients/{$core.iam.clients.$grafana_oidc:uuid}/actions/get_token/invoke"
  api_url: f"{$core.vs.variables.$var_core_api_url:value}/api/core/v1/iam/clients/{$core.iam.clients.$grafana_oidc:uuid}/actions/userinfo"
  root_url: $core.vs.variables.$var_grafana_root_url:value
  scopes: "openid profile email"
```

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `client_id` | string | yes | 1–128 chars, IAM client id |
| `client_secret` | string | yes | 1–256 chars, from `$core.secret.passwords` |
| `auth_url` | string | yes | 1–2048 chars, OIDC authorization endpoint |
| `token_url` | string | yes | 1–2048 chars, token exchange endpoint |
| `api_url` | string | yes | 1–2048 chars, UserInfo endpoint |
| `root_url` | string | yes | 1–2048 chars, public base URL of Grafana (`GF_SERVER_ROOT_URL`); must match the IAM Idp callback |
| `scopes` | string | no | Default: `openid profile email` (max 256 chars) |

A full OIDC setup also requires an IAM client, an Idp with a callback
URL matching Grafana's `/login/generic_oauth` path, and the secret —
see `observability.yaml.j2` for the complete wiring.

---

## How to add a datasource to Grafana

Datasources are child resources of a Grafana instance. They are declared
under `$grafanaaas.types.grafana.instances.$<instance>.datasources`.

Two datasource types are supported:

| Type | Use case |
| ------ | ---------- |
| `prometheus` | VictoriaMetrics or any Prometheus-compatible backend |
| `victoriametrics-logs-datasource` | VictoriaLogs (native plugin, not Loki-compatible) |

```yaml
resources:
  $grafanaaas.types.grafana.instances.$my_grafana.datasources:
    metrics_ds:
      name: "victoria-metrics"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $grafanaaas.types.grafana.instances.$my_grafana:uuid
      type: "prometheus"
      url: $victoriaaas.types.victoria.instances.$my_victoria:metrics_endpoint
      is_default: true
      auth:
        kind: basic
        username: "grafana-reader"
        password: $core.secret.passwords.$vmauth_reader_password:value
    logs_ds:
      name: "victoria-logs"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $grafanaaas.types.grafana.instances.$my_grafana:uuid
      type: "victoriametrics-logs-datasource"
      url: $victoriaaas.types.victoria.instances.$my_victoria:logs_endpoint
      is_default: false
      auth:
        kind: basic
        username: "grafana-reader"
        password: $core.secret.passwords.$vmauth_reader_password:value
```

**Fields:**

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `name` | string | yes | Datasource name in Grafana (1–255 chars) |
| `project_id` | UUID | yes | EM project id |
| `instance` | UUID | yes | Parent Grafana instance UUID |
| `type` | enum | yes | `prometheus` or `victoriametrics-logs-datasource` |
| `url` | string | yes | Backend URL (1–512 chars) |
| `is_default` | bool | no | Default: `false` |
| `auth` | kind model | no | Datasource auth (see below); omit for unauthenticated backends |

**`auth` kinds** (polymorphic, selected by `kind`, `controlplane/dm/datasource_auth.py`):

| Kind | Fields | Description |
| ------ | -------- | ------------- |
| `basic` | `username` (1–128), `password` (1–256) | HTTP Basic Auth rendered as `basicAuth`/`basicAuthUser`/`secureJsonData.basicAuthPassword` in the Grafana provisioning YAML |

The `url` field is the cross-plugin wiring point — it typically references
a Victoria instance's `metrics_endpoint` or `logs_endpoint` via
`$path:field` syntax. When the Victoria instance is protected by vmauth
Basic Auth, the datasource `auth` must carry the same `username`/`password`
pair so Grafana can authenticate its read requests.

> **Note:** VictoriaLogs does **not** expose a Loki-compatible API, so the
> native `victoriametrics-logs-datasource` Grafana plugin is required
> (installed in the DP image via `grafana cli plugins install`).

---

## How to add a dashboard to Grafana

Adding a dashboard is a two-step process:

1. **Declare a dashboard artifact** — a top-level resource that stores the
   dashboard definition and how to fetch it (`$grafanaaas.types.grafana.dashboards`).
2. **Bind the artifact to a Grafana instance** — a child resource that
   references the artifact's `version_ref` and specifies which Grafana
   folder to place it in (`$grafanaaas.types.grafana.instances.$<instance>.dashboards`).

```yaml
resources:
  # Step 1: dashboard artifact (where to fetch the dashboard JSON from)
  $grafanaaas.types.grafana.dashboards:
    my_dashboard:
      name: "my-dashboard"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      source:
        kind: bundled              # see "Dashboard source kinds" below
        dashboard_id: 1860
        revision: 31

  # Step 2: bind the artifact to a Grafana instance
  $grafanaaas.types.grafana.instances.$my_grafana.dashboards:
    my_dashboard_binding:
      name: "my-dashboard"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $grafanaaas.types.grafana.instances.$my_grafana:uuid
      folder: ""                   # empty = root folder
      version_ref: $grafanaaas.types.grafana.dashboards.$my_dashboard:version_ref
```

**Binding fields:**

| Field | Type | Required | Description |
| ------- | ------ | ---------- | ------------- |
| `name` | string | yes | Dashboard name in Grafana (1–255 chars) |
| `project_id` | UUID | yes | EM project id |
| `instance` | UUID | yes | Parent Grafana instance UUID |
| `folder` | string | no | Grafana folder name (empty = root) |
| `version_ref` | string | yes | From the dashboard artifact's `version_ref` |

The builder resolves the `version_ref`, fetches the dashboard content from
the artifact's source, and provisions it on the Grafana DP node as a JSON
file under `/var/lib/grafana/dashboards/exordos/<folder>/`. When the
`version_ref` changes (e.g. a new dashboard revision), the builder
re-fetches and re-provisions automatically.

---

## Dashboard source kinds

The `source` field on a dashboard artifact is polymorphic — it uses a
kind model selector with three implementations
(`controlplane/dm/sources.py`):

### `bundled` — Grafana community catalog

Downloads a dashboard from the Grafana community catalog by its numeric ID.

```yaml
source:
  kind: bundled
  dashboard_id: 1860      # numeric ID from grafana.com
  revision: 31            # specific revision
```

- **Digest:** `grafana_{dashboard_id}_{revision}`
- **Fetch URL:** `https://grafana.com/api/dashboards/{id}/revisions/{rev}/download`
- **Use case:** Pre-built community dashboards (e.g. Node Exporter Full #1860)

### `urn` — Core repo artifact

Fetches dashboard JSON from an Exordos Core repo artifact, resolved by URN
(`urn:artifacts:<uuid>`) against Core's `RepoArtifact` registry. The control
plane never fetches an arbitrary caller-supplied URL for this source kind —
the URN can only resolve to an artifact a trusted `Repository` has already
indexed, which is why this replaced the old `url` source kind (SSRF risk).

```yaml
source:
  kind: urn
  urn: "urn:artifacts:12345678-1234-1234-1234-123456789012"
```

- **Digest:** the URN itself
- **Use case:** Dashboards published by another element/repository as a
  trusted platform artifact (e.g. that element's own build publishes a
  dashboard JSON artifact under this URN)

### `raw` — inline JSON

Stores the dashboard JSON inline in the manifest.

```yaml
source:
  kind: raw
  content:
    title: "My Dashboard"
    panels:
      - type: "stat"
        title: "Total Requests"
        targets:
          - expr: "rate(http_requests_total[5m])"
    # ... full Grafana dashboard JSON schema
```

- **Digest:** SHA-256 of the stable JSON serialization
- **Use case:** Custom dashboards defined directly in the manifest

### Caching

All source kinds cache their fetched content in memory after the first
successful fetch (`AbstractDashboardSource._cached_dashboard`). Repeated
builder iterations don't re-fetch from external providers. The cache is
per-object and not persisted — if the builder process restarts, the next
iteration re-fetches.

---

## How to wire Victoria + Grafana together

When both instances are declared in the same manifest, datasource `url`
fields can reference the Victoria instance's endpoints directly via
intra-manifest `$path:field` syntax — no `imports:` entry needed.

This example mirrors `observability.yaml.j2`: Victoria is protected by
vmauth Basic Auth, and the Grafana datasources carry the same
`username`/`password` pair to authenticate read requests.

```yaml
resources:
  # vmauth Basic Auth password — shared by the Victoria instance and
  # the Grafana datasources that read from it.
  $core.secret.passwords:
    vmauth_reader_password:
      name: "vmauth_reader_password"
      description: "vmauth Basic Auth password for Grafana reader"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      method: "AUTO_URL_SAFE"
      constructor:
        kind: plain
      default_length: 32

  $victoriaaas.types.victoria.instances:
    victoria:
      name: "victoria"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      cpu: 2
      ram: 2048
      metrics_disk_size: 20
      logs_disk_size: 20
      retention_period: "30d"
      replicas: 1
      vmauth:
        kind: basic
        username: "grafana-reader"
        password: $core.secret.passwords.$vmauth_reader_password:value
      version_ref: $my_manifest.imports.$victoria_v1:version_ref

  $core.secret.passwords:
    grafana_admin_password:
      name: "grafana_admin_password"
      description: "Grafana admin password"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      method: "AUTO_URL_SAFE"
      constructor:
        kind: plain
      default_length: 32

  $grafanaaas.types.grafana.instances:
    grafana:
      name: "grafana"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      cpu: 2
      ram: 2048
      root_disk_size: 20
      replicas: 1
      auth:
        kind: password
        password: $core.secret.passwords.$grafana_admin_password:value
      version_ref: $my_manifest.imports.$grafana_v13:version_ref

  # Datasources reference the Victoria instance's endpoints directly
  # and authenticate with the same vmauth Basic Auth pair.
  $grafanaaas.types.grafana.instances.$grafana.datasources:
    victoria_metrics_ds:
      name: "victoria-metrics"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $grafanaaas.types.grafana.instances.$grafana:uuid
      type: "prometheus"
      url: $victoriaaas.types.victoria.instances.$victoria:metrics_endpoint
      is_default: true
      auth:
        kind: basic
        username: "grafana-reader"
        password: $core.secret.passwords.$vmauth_reader_password:value
    victoria_logs_ds:
      name: "victoria-logs"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $grafanaaas.types.grafana.instances.$grafana:uuid
      type: "victoriametrics-logs-datasource"
      url: $victoriaaas.types.victoria.instances.$victoria:logs_endpoint
      is_default: false
      auth:
        kind: basic
        username: "grafana-reader"
        password: $core.secret.passwords.$vmauth_reader_password:value

  # Dashboard artifact + binding
  $grafanaaas.types.grafana.dashboards:
    node_exporter_full:
      name: "node-exporter-full"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      source:
        kind: bundled
        dashboard_id: 1860
        revision: 31

  $grafanaaas.types.grafana.instances.$grafana.dashboards:
    node_exporter:
      name: "node-exporter-full"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $grafanaaas.types.grafana.instances.$grafana:uuid
      folder: ""
      version_ref: $grafanaaas.types.grafana.dashboards.$node_exporter_full:version_ref

imports:
  victoria_v1:
    element: "$victoriaaas"
    kind: "resource"
    link: "$victoriaaas.types.victoria.versions.$v1"
  grafana_v13:
    element: "$grafanaaas"
    kind: "resource"
    link: "$grafanaaas.types.grafana.versions.$v13"
```

This is the pattern used by `observability.yaml.j2` (which additionally
uses OIDC auth for Grafana and profile-backed disk sizes — see the
manifest for the full version).

---

## How to reference resources from another manifest

If your manifest is in a different element and wants to attach resources
to an instance owned by another manifest, use the `imports:` section to
bring in the instance, then reference it.

### Adding a datasource

```yaml
resources:
  # Add a datasource to a Grafana instance declared in the observability
  # element's manifest. The nested collection is addressed through the
  # import, not the original resource path.
  $my_manifest.imports.$grafana_instance.datasources:
    extra_ds:
      name: "external-prometheus"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $my_manifest.imports.$grafana_instance:uuid
      type: "prometheus"
      url: "http://10.20.0.50:9090"
      is_default: false

imports:
  grafana_instance:
    element: "$observability"
    kind: "resource"
    link: "$grafanaaas.types.grafana.instances.$grafana"
```

### Adding a dashboard

```yaml
resources:
  # Step 1: declare the dashboard artifact (where to fetch the JSON from)
  $grafanaaas.types.grafana.dashboards:
    custom_dashboard:
      name: "custom-dashboard"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      source:
        kind: raw
        content:
          title: "Custom Dashboard"
          panels:
            - type: "stat"
              title: "Total Requests"
              targets:
                - expr: "rate(http_requests_total[5m])"

  # Step 2: bind the artifact to the external Grafana instance
  $my_manifest.imports.$grafana_instance.dashboards:
    custom_dashboard_binding:
      name: "custom-dashboard"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      instance: $my_manifest.imports.$grafana_instance:uuid
      folder: "Custom"
      version_ref: $grafanaaas.types.grafana.dashboards.$custom_dashboard:version_ref

imports:
  grafana_instance:
    element: "$observability"
    kind: "resource"
    link: "$grafanaaas.types.grafana.instances.$grafana"
```

The `exports:` section in `observability.yaml.j2` exposes both the
Victoria and Grafana instances for this purpose.

Two details that are easy to get wrong:

- **The import `link` is the exported resource's own link, not the
  export path.** `observability.yaml.j2` exports `grafana_instance`
  with `link: "$grafanaaas.types.grafana.instances.$grafana"` — that
  same string goes verbatim into the importing manifest's `imports:
  grafana_instance: link:`. The platform forms the lookup key as
  `$<element>.<link>` internally; writing `$observability.exports.$grafana_instance`
  here produces a doubled `$observability.$observability.exports...` key
  that matches no export and fails at install time.
- **Nested collections on an imported resource are addressed through
  the import, not the original resource path.** A datasource or
  dashboard binding on an imported Grafana instance is declared as
  `$<your_manifest>.imports.$<import_name>.datasources` / `.dashboards`,
  not `$grafanaaas.types.grafana.instances.$<something>.datasources`.
  The imported resource is registered in the element engine under
  `$<your_manifest>.imports.$<import_name>`, and the manifest link
  resolver looks it up there.

---
