<!--
    Copyright 2026 Genesis Corporation

    Licensed under the Apache License, Version 2.0 (the "License"); you may
    not use this file except in compliance with the License. You may obtain
    a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
    License for the specific language governing permissions and limitations
    under the License.
-->

# Securing READ Access to Victoria Endpoints via `vmauth` + Exordos IAM

> **Status: Implemented (Option A + Basic Auth).** This document captures the
> design for closing the currently open READ endpoints of the Victoria
> (VictoriaMetrics + VictoriaLogs) instance deployed by
> `observability.yaml.j2`, using `vmauth` as a read-only reverse proxy.
> The implemented variant uses static Basic Auth credentials (a
> `grafana-reader` user with a read-only path ACL) rather than IAM-issued
> JWTs — the JWT/IAM integration variants below are documented for a
> future Phase B/C evolution. See `exordos_observability/victoria/` for
> the implementation (infra builder, `vmauth` systemd unit, datasource
> auth fields in the Grafana model).

## Problem statement

The deployed `observability.yaml.j2` exposes VictoriaMetrics and VictoriaLogs
HTTP endpoints with **no authentication**. Anyone on the dataplane network who
knows the IP or the published DNS name can read every metric and every log of
the entire platform.

Confirmed against the repo state **before** this implementation was applied
(now resolved — see the implementation in
`exordos_observability/victoria/controlplane/infra/` and
`exordos_observability/grafana/controlplane/dm/auth.py`):

- **VictoriaMetrics** was started as
  `victoria-metrics -retentionPeriod=... -storageDataPath=... -httpListenAddr=:8428`
  with no auth flags and bound `0.0.0.0:8428`.
- **VictoriaLogs** was started as
  `victoria-logs ... -httpListenAddr=:9428` with no auth flags and bound
  `0.0.0.0:9428`.
- The CP-delivered env file contained only `retention_period`,
  `storage_data_path` and `http_listen_addr` — no credentials.
- A DNS A record `victoria-storage.local.genesis-core.tech` was published in
  the manifest and pointed at the Victoria node IP.
- Grafana datasource provisioning rendered only `name`, `type`, `url`,
  `access: proxy`, `isDefault`, `editable` — no `basicAuth`, no auth headers.
- The `GrafanaDatasource` CP model had only `name`, `type`, `url`, `is_default`
  — no auth fields.

**Result (before the fix):** any reachable client could read all metrics and
all logs of the entire platform, and could also write/delete (see "Out of
scope" below).

## Scope

- **Protect:** READ endpoints on VictoriaMetrics (`:8428`) and VictoriaLogs
  (`:9428`) — `/api/v1/query*`, `/api/v1/series`, `/api/v1/label/*`,
  `/select/*`, `/vmui/*`, etc. Only verified users (holders of a valid token
  issued by Exordos Core IAM) should be able to read.
- **Do NOT touch:** the WRITE path (`/write`, `/api/v1/write`, `/insert/*`).
  Base-image `vmagent`/`vlagent` agents on other platform nodes must keep
  pushing metrics and logs without credentials. This rules out native
  `-httpAuth.username/password` (see Option D below — it is a global flag that
  protects read AND write AND admin simultaneously).
- **Out of scope for this doc:** write-path protection (fake-metric/log
  injection, sabotage), admin endpoints (`/snapshot*`, `/delete_series`,
  `/flags`), TLS transport encryption (mentioned as future hardening, not the
  focus). These are tracked as future Phase B / Phase C at the end of the doc.

## Threat surface (read only)

Endpoints currently reachable without any credential:

**VictoriaMetrics (`:8428`):**

- `GET /api/v1/query?query=...` — instant PromQL/MetricsQL query
- `GET /api/v1/query_range?query=...&start=...&end=...` — range query
- `GET /api/v1/series?...` — list time series matching a selector
- `GET /api/v1/labels` — list label names
- `GET /api/v1/label/{name}/values` — list values for a label
- `GET /prometheus/api/v1/*` — Prometheus-compatible aliases of the above
- `GET /graph`, `/vmui/*` — built-in UIs (read access to query results)

**VictoriaLogs (`:9428`):**

- `GET /select/logsql/query?query=...` — LogsQL query
- `GET /select/logsql/tail?query=...` — live tail
- `GET /select/*` — other select API endpoints
- `GET /vmui/*` — built-in UI

Example of what is currently possible from any reachable host:

```bash
# Read every metric of the platform
curl 'http://victoria-storage.local.genesis-core.tech:8428/api/v1/query?query=up'

# Read every log of the platform
curl 'http://victoria-storage.local.genesis-core.tech:9428/select/logsql/query?query=*'
```

## Upstream capabilities

### VictoriaMetrics / VictoriaLogs native auth flags

- `-httpAuth.username` / `-httpAuth.password` — **global** Basic Auth for all
  HTTP endpoints (read + write + admin). Available on both VM single-node and
  all VictoriaLogs components. **Not usable for this scope** because it also
  closes the write path (see Option D).
- `-metricsAuthKey`, `-snapshotAuthKey`, `-deleteAuthKey`, `-flagsAuthKey`
  (VM only) — per-admin-endpoint auth keys. These protect `/metrics`,
  `/snapshot*`, `/api/v1/admin/tsdb/delete_series`, `/flags` respectively.
  They do **not** protect the query API (`/api/v1/query*`), so they are not a
  solution for the read path. Complementary hardening for admin endpoints,
  future work.
- `-tls` / `-tlsCertFile` / `-tlsKeyFile` — TLS server. mTLS via client-cert
  verification. Future hardening, not the focus here.

### `vmauth` (recommended proxy)

`vmauth` is the VictoriaMetrics-ecosystem HTTP reverse proxy. Relevant
capabilities:

- **Path-based ACLs** via `src_paths` (regex list) and `url_map` (per-path
  routing to different backends). This is what lets us expose only read paths
  to the Grafana reader while leaving write paths open for agents.
- **Auth modes:**
  1. `username` + `password` — Basic Auth (static credentials in config).
  2. `bearer_token` — static token, string-match against incoming
     `Authorization: Bearer ...`. No signature/expiry validation.
  3. `jwt` + `public_keys` (v1.137.0+) — verifies JWT signature against
     RSA/ECDSA public keys embedded in config. Supports `match_claims` for
     RBAC (e.g. `role: viewer` → read-only `url_map`). **HS256 is not
     supported** — vmauth JWT requires RSA/ECDSA keys.
  4. `jwt` + `oidc.issuer` (OIDC Discovery, v1.138.0+) — vmauth fetches
     `{issuer}/.well-known/openid-configuration`, discovers `jwks_uri`,
     downloads the JWKS, verifies JWT signatures, and rotates keys every 5
     minutes. The JWT `iss` claim must match the configured `issuer` exactly.
     The discovery URL is hardcoded to `{issuer}/.well-known/openid-configuration`
     — there is no way to specify a custom discovery path.
- **TLS termination**, mTLS, IP filters.
- **Config reload** via SIGHUP or `POST /internal/-/reload`.
- JWT auth cannot be combined with `bearer_token`/`username`/`password` in the
  same `users` entry.

### nginx / caddy (generic alternatives)

A generic reverse proxy can do Basic Auth + read-only `location` filters.
JWT validation in nginx requires `njs` or an `oauth2-proxy` sidecar, which is
more complex than vmauth's native JWT support. See Option B.

## Options compared (proxy layer)

### Option A — `vmauth` reverse proxy with read-only path ACL (RECOMMENDED)

Architecture:

- VM/VL bind to `127.0.0.1:8428/9428` — not directly reachable from the
  network.
- `vmauth` listens on `0.0.0.0:8428` (or a dedicated port), proxies to
  localhost VM/VL.
- vmauth config defines a user for Grafana with `src_paths` allowing **only
  read paths** — `/api/v1/query.*`, `/api/v1/series`, `/api/v1/label/[^/]+/values`,
  `/select/.*`. Write paths (`/write`, `/api/v1/write`, `/insert/.*`) and admin
  paths are NOT in the Grafana user's ACL.
- Write path stays open: agents push directly to VM/VL on `127.0.0.1` if
  co-located, or via a separate `vmauth` user with a write-only ACL and no
  auth (or a shared token). The simplest option for the current scope is a
  second `vmauth` user `agent-writer` with write-only `src_paths` and no auth —
  this keeps agents unchanged while still closing read to anonymous clients.

Pros:

- Native to the Victoria ecosystem; aligns with upstream best practice.
- Read-only ACL — closes read without touching the write path (satisfies the
  locked scope).
- Path to future hardening: TLS, mTLS, per-user tokens, integration with
  Exordos IAM (see "IAM integration with vmauth" below).
- vmauth config supports reload via SIGHUP or `/internal/-/reload`.

Cons:

- New component: a sidecar systemd unit on the Victoria node (minimal blast
  radius for Phase 1) or a separate small NodeSet.
- New config file (`vmauth.yaml`) must be delivered by the CP — a new `Config`
  resource + on-change handler to reload vmauth.
- vmauth binary must be installed in the Victoria DP image
  (`victoria_dp_install.sh` — download from VictoriaMetrics releases, same
  pattern as the VM/VL binaries).

### Option B — nginx (or caddy) reverse proxy with Basic Auth + read-only location filter

Same architecture as Option A, but nginx instead of vmauth:

- VM/VL bind to `127.0.0.1`.
- nginx `server` block with `auth_basic` on read `location`s
  (`/api/v1/query`, `/api/v1/query_range`, `/api/v1/series`, `/api/v1/label/`,
  `/select/`), proxying to localhost VM/VL.
- Write `location`s (`/write`, `/api/v1/write`, `/insert/`) without
  `auth_basic` — open for agents.
- `.htpasswd` file with the Grafana reader credential, delivered by CP.

Pros:

- Familiar tool, no Victoria-specific knowledge needed.
- Read-only ACL achievable via `location` blocks.
- TLS trivial to add later.

Cons:

- Not native to the Victoria ecosystem — no future path to `vmgateway`/IAM
  integration.
- ACL written as nginx location regex — easier to misconfigure than vmauth's
  explicit `src_paths` list.
- JWT validation requires `njs` or an `oauth2-proxy` sidecar — significantly
  more complex than vmauth's native JWT support if IAM integration is wanted
  later.
- Same new-component + config-delivery overhead as Option A.

### Option C — Network isolation only (REJECTED, documented for completeness)

Bind VM/VL to a specific internal interface, firewall `:8428`/`:9428` to
Grafana + agent source IPs, unpublish or restrict the `victoria-storage` DNS
A record.

Rejected because: does not provide per-user auth, does not satisfy "only
verified users", fragile (depends on network topology), no defense against
lateral movement on the DP network.

### Option D — Native Basic Auth (REJECTED for this scope, documented for completeness)

`-httpAuth.username` / `-httpAuth.password` on VM/VL. This would be the
simplest possible fix, but it is a **global** flag — it protects read AND
write AND admin simultaneously. Enabling it would break base-image
`vmagent`/`vlagent` agents that push without credentials, which is explicitly
out of scope here. Documented as the candidate for a future Phase B that also
addresses the write path.

## IAM integration with vmauth

The natural way to issue and validate the tokens used by vmauth is Exordos
Core IAM, which is a full OIDC provider. This section documents the research
findings and two integration variants.

### vmauth auth modes relevant to IAM

1. **`bearer_token`** — static token in config, string-match against incoming
   `Authorization: Bearer ...`. No signature/expiry validation. **Not
   recommended** — gives the illusion of using IAM without real security
   properties (no validation, no rotation, no RBAC). See "Variant II" below.
2. **`jwt` + `public_keys`** (v1.137.0+) — verifies JWT signature against
   RSA/ECDSA public keys embedded in config. Supports `match_claims` for RBAC
   (e.g. `role: viewer` → read-only `url_map`). **HS256 is not supported** —
   vmauth JWT requires RSA/ECDSA keys.
3. **`jwt` + `oidc.issuer`** (OIDC Discovery, v1.138.0+) — vmauth fetches
   `{issuer}/.well-known/openid-configuration`, discovers `jwks_uri`,
   downloads the JWKS, verifies JWT signatures, and rotates keys every 5
   minutes. The JWT `iss` claim must match the configured `issuer` exactly.
   The discovery URL is hardcoded to `{issuer}/.well-known/openid-configuration`.

### Exordos Core IAM capabilities

Verified against `../exordos_core/exordos_core/user_api/iam/dm/models.py`:

- **`IamClient`** (line 1040) — OIDC client with `client_id`, `secret`
  (hashed), `signature_algorithm` (`HS256` | `RS256`). RS256 stores
  `private_key` + `public_key`. Manifest-declarable as `$core.iam.clients`.
- **`Idp`** (line 1811) — OIDC identity provider, linked to an `IamClient`.
  Manifest-declarable as `$core.iam.idp`.
- **`Idp.get_wellknown_info()`** (line 1863) — standard OIDC discovery
  document: `issuer`, `authorization_endpoint`, `token_endpoint`,
  `userinfo_endpoint`, `jwks_uri`, `claims_supported`
  (`preferred_username`, `sub`, `iss`, `name`, `email`).
- **`IamClient.get_jwks()`** (line 1466) — JWKS endpoint, serves RS256 public
  keys. Exposed at `/v1/iam/clients/{uuid}/actions/jwks`.
- **Token issuance:** `get_token_by_password()` (password grant),
  `get_token_by_authorization_code()` (OIDC code flow),
  `get_token_by_refresh_token()`. Supports `scope`, `ttl`, `refresh_ttl`, and
  service account tokens.
- The same `$core.iam.clients` + `$core.iam.idp` pattern is already used in
  [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md) for Grafana UI
  authentication.

### Critical incompatibility: OIDC Discovery fails out of the box

vmauth's OIDC Discovery mode hardcodes the discovery URL as
`{issuer}/.well-known/openid-configuration`. In Exordos Core:

- The **issuer** in the discovery document is
  `{app_url}/v1/iam/clients/{iam_client.uuid}` (see `Idp.get_wellknown_info`,
  line 1868).
- The **discovery document** is served at
  `{app_url}/v1/iam/idp/{idp.uuid}/.well-known/openid-configuration`
  (see `Idp.well_known_endpoint`, lines 1858-1861).

So vmauth configured with `oidc.issuer: "{app_url}/v1/iam/clients/{uuid}"`
will fetch `{app_url}/v1/iam/clients/{uuid}/.well-known/openid-configuration`
— **this endpoint does not exist**. OIDC Discovery mode does not work with
Exordos IAM directly. This is the root cause of the two-variant split below.

### Variant I — vmauth JWT with manual RS256 public keys (no Core changes)

- Declare an `IamClient` with `signature_algorithm: RS256` in the observability
  manifest (new `$core.iam.clients` + `$core.iam.idp`, analogous to
  [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md)).
- The CP fetches the JWKS from `/v1/iam/clients/{uuid}/actions/jwks`,
  extracts the RSA public key, and embeds it in `vmauth.yaml` as `public_keys`.
- vmauth verifies JWT signatures; `match_claims` provides RBAC
  (e.g. `role: viewer` → read-only `url_map`).
- Tokens are issued by IAM (password grant / OIDC code flow / service account
  token).
- The CP must periodically re-fetch the JWKS and update `vmauth.yaml` + reload
  vmauth on key rotation.

Pros:

- No `exordos_core` changes — entirely contained in this repo.
- Per-user tokens with real JWT validation (signature + expiry).
- RBAC via claims.
- Reuses the `$core.iam.clients` / `$core.iam.idp` manifest pattern already
  designed for Grafana OIDC.

Cons:

- No automatic key rotation — the CP must poll the JWKS endpoint and push an
  updated `vmauth.yaml` when the RS256 public key changes. This is a new
  reconciliation responsibility in the Victoria infra builder.

### Variant III — fix OIDC Discovery in `exordos_core` (cross-repo)

- Add a route `{app_url}/v1/iam/clients/{uuid}/.well-known/openid-configuration`
  in `exordos_core` that serves the discovery document (currently only served
  on the Idp path).
- After this change, vmauth with `oidc.issuer: "{app_url}/v1/iam/clients/{uuid}"`
  works natively: it fetches the discovery document, then the JWKS, verifies
  JWT signatures, and rotates keys every 5 minutes — all without CP
  intervention.
- The issuer already equals the client URL, so the discovery document served
  at the client path is self-consistent (no issuer/URL mismatch).

Pros:

- Proper OIDC; automatic key rotation; native vmauth integration.
- Benefits any OIDC client of Exordos IAM, not just vmauth — a general
  improvement to the platform.

Cons:

- Change in `exordos_core` — a separate repo, separate PR, separate review.
- Slightly larger blast radius than Variant I.

### Variant II — static `bearer_token` (REJECTED)

vmauth's `bearer_token` mode does a plain string match against the incoming
`Authorization` header. It does not validate the JWT signature, does not
check expiry, does not support rotation or RBAC. Using it would give the
appearance of integrating with IAM (a token is issued by IAM and put in the
vmauth config) without any of IAM's actual security properties. **Rejected.**

### Service-to-service vs per-user (Grafana side)

- **Service-to-service (Grafana → vmauth):** the Grafana server authenticates
  with a single token issued by IAM. The token is placed in the datasource
  provisioning via `jsonData.httpHeaderName1: "Authorization"` +
  `secureJsonData.httpHeaderValue1: "Bearer <token>"`. Works with Variant I or
  Variant III. The CP rotates the token in the provisioning config when it
  expires. This is the recommended Phase A shape.
- **Per-user (browser → Grafana → vmauth):** Grafana forwards the logged-in
  user's OIDC token to the datasource (OBO / Forward OAuth Identity). This
  requires Grafana OIDC login ([`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md)
  implemented) and the datasource configured with forward-identity. vmauth
  then validates a per-user JWT. This is full per-user access control — a
  combination of both design docs and more moving parts. Documented as a
  future evolution (Phase C), not Phase A.

### Recommendation

- **Proxy layer:** Option A (`vmauth`).
- **IAM auth:** both Variant I and Variant III are presented in full with
  tradeoffs. The decision is deferred to design-doc review. Variant II is
  rejected. Per-user forwarding is noted as a future evolution dependent on
  [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md).

## Implementation outline for Option A + Basic Auth (implemented)

This section describes the changes that were applied for the implemented
Basic Auth variant. The JWT/IAM variants (Variant I, Variant III) below are
documented for a future Phase B/C evolution and are **not** implemented.

### Victoria DP image

- **`exordos/images/victoria_dp_install.sh`**: download the `vmauth` binary
  from VictoriaMetrics releases (same pattern as the VM/VL binaries already
  downloaded there), install to `/usr/bin/vmauth`.
- **`etc/systemd/exordos-metapaas-vmauth.service`** (NEW): a sidecar systemd
  unit on the Victoria node, gated on a CP-delivered config file
  (`ConditionPathExists=/etc/exordos_metapaas/vmauth.yaml`), running
  `vmauth -auth.config=/etc/exordos_metapaas/vmauth.yaml -httpListenAddr=:8428`.
- **`etc/systemd/exordos-metapaas-victoriametrics.service`** and
  **`etc/systemd/exordos-metapaas-victorialogs.service`**: change the listen
  address to `127.0.0.1` (via `VM_HTTP_LISTEN_ADDR`/`VL_HTTP_LISTEN_ADDR` in
  `victoria.env`) so VM/VL are not directly reachable from the network.

### Victoria control plane

- **`exordos_observability/victoria/controlplane/infra/dm/models.py`**: add a
  second `Config` resource for `vmauth.yaml` (path
  `/etc/exordos_metapaas/vmauth.yaml`), with an `OnChangeShell` that reloads
  vmauth (`systemctl reload exordos-metapaas-vmauth` or
  `kill -HUP $(pidof vmauth)`).
- **`exordos_observability/victoria/controlplane/infra/services/builder.py`**:
  render `vmauth.yaml` with:
  - a `grafana-reader` user with `jwt.public_keys` (the RS256 public key
    fetched from Core JWKS) and a read-only `url_map`
    (`src_paths: ["/api/v1/query.*", "/api/v1/series", "/api/v1/label/[^/]+/values", "/select/.*"]` →
    `url_prefix: "http://127.0.0.1:8428"` for VM and
    `url_prefix: "http://127.0.0.1:9428"` for VL, routed by path);
  - an `agent-writer` user with write-only `src_paths`
    (`/write`, `/api/v1/write`, `/insert/.*`) and no auth, so base-image
    agents keep working unchanged;
  - `match_claims` for RBAC if per-user roles are wanted.
- The CP must periodically re-fetch the JWKS from
  `/v1/iam/clients/{uuid}/actions/jwks` and re-render `vmauth.yaml` when the
  RS256 public key changes. This is a new reconciliation responsibility —
  likely a background task in the Victoria infra builder that polls the Core
  API and triggers a config re-delivery on diff.

### Observability manifest

- **`exordos/manifests/observability.yaml.j2`**: declare an `IamClient` with
  `signature_algorithm: RS256` and an `Idp` in `$core.iam.clients` /
  `$core.iam.idp`, analogous to
  [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md). The RS256 key pair is
  managed by Core's secret infrastructure; the CP reads the public key via
  the JWKS endpoint.
- The Victoria instance declaration passes the IamClient UUID to the infra
  builder so it can fetch the JWKS.

### Grafana control plane + DP

- **`exordos_observability/grafana/controlplane/dm/models.py`**: add optional
  `auth_header_name` and `auth_header_value` fields to `GrafanaDatasource`
  (value `HIDDEN` in the API, sourced from `$core.secret.passwords` or a
  token-issuance flow). Alternatively `basic_auth_user` + `basic_auth_password`
  if Basic Auth at vmauth is preferred over JWT for the service-to-service
  path.
- **`exordos_observability/grafana/migrations/`**: a new migration adding the
  auth columns to `grafana_datasources`.
- **`exordos_observability/grafana/dataplane/driver.py`**: when a datasource
  has auth fields, render `jsonData.httpHeaderName1: "Authorization"` +
  `secureJsonData.httpHeaderValue1: "Bearer <token>"` (or `basicAuth: true` +
  `basicAuthUser` + `secureJsonData.basicAuthPassword`) in the provisioning
  YAML, following the existing `_render_provisioning_yaml` pattern.
- **`exordos/manifests/observability.yaml.j2`** datasource declarations: add
  the auth fields to `victoria_metrics_ds` and `victoria_logs_ds`, and change
  `url` to point at vmauth (same port `:8428` since vmauth fronts both
  backends via path routing, or a dedicated vmauth port if preferred).

### Tests

- Unit tests for the new `GrafanaDatasource` auth fields (model
  deserialization + `HIDDEN` field permission).
- Unit tests for `vmauth.yaml` rendering in the Victoria infra builder
  (read-only ACL, write-only ACL, RS256 public key embedding).
- Unit tests for the Grafana DP driver rendering auth headers/basicAuth in
  the provisioning YAML.

## Implementation outline for Option A + Variant III (delta over Variant I)

- **`exordos_core`** (cross-repo PR): add a route
  `{app_url}/v1/iam/clients/{uuid}/.well-known/openid-configuration` that
  serves the OIDC discovery document. The document is already produced by
  `Idp.get_wellknown_info()`; the new route just exposes it at the client
  path in addition to the existing Idp path. The issuer already equals the
  client URL, so the document is self-consistent at the new path.
- **`vmauth.yaml`** uses `jwt.oidc.issuer` instead of `jwt.public_keys` — no
  JWKS polling in the CP; vmauth performs discovery and key rotation itself
  every 5 minutes.
- Everything else (DP image, systemd units, Grafana datasource auth fields,
  manifest declarations) is identical to Variant I.

## Implementation outline for Option B (nginx)

Same shape as Option A, but nginx instead of vmauth. JWT validation in nginx
requires `njs` or an `oauth2-proxy` sidecar, which is significantly more
complex than vmauth's native JWT support. For this reason Option B is
presented as a fallback for teams that prefer a generic proxy, not as the
primary recommendation. The Basic Auth variant of Option B (no JWT) is
straightforward but does not integrate with IAM.

## Verification

### Implemented (Basic Auth variant)

- `tox -e ruff-check` passes.
- `tox -e py312` passes (all existing + new unit tests, including
  `test_vmauth.py`).
- vmauth config reload on CP-delivered change (SIGHUP or `/internal/-/reload`).

### Future implementation (JWT/IAM variants — not yet applied)

- Manual curl matrix against the live stand:
  - read with valid JWT → `200`
  - read without / with invalid JWT → `401`
  - write without credential → `200` (agents keep working)
  - write with the Grafana-reader JWT → `403` (read-only ACL blocks write)
- For Variant I: JWKS polling detects an RS256 key rotation and re-delivers
  `vmauth.yaml` without manual intervention.
- For Variant III: vmauth logs show successful OIDC discovery + JWKS fetch
  from the Core client path.

## Risks / considerations

1. **vmauth is a new component** in the Victoria DP image. This is a
   build-time change (`victoria_dp_install.sh` + `make build`) that the
   future implementation will perform. The vmauth binary is downloaded from
   VictoriaMetrics releases using the same GitHub-API asset-name pattern
   already used for VM/VL — see `docs/DESIGN.md` risk about GitHub API asset
   pattern matching, which applies equally here.

2. **Binding VM/VL to `127.0.0.1` is a behavior change** for any existing
   direct consumer other than Grafana and the co-located vmauth. The future
   implementation must audit for other consumers (e.g. direct `vmui` access
   by operators) and document the migration. Operators who need direct read
   access would go through vmauth with a valid token.

3. **JWKS polling (Variant I)** adds a new reconciliation responsibility to
   the Victoria infra builder. The polling interval must be short enough to
   pick up key rotation before old tokens expire, but not so short that it
   loads the Core API. Variant III avoids this entirely.

4. **Cross-repo change (Variant III)** touches `exordos_core`, which has its
   own review process and CI. The change is small (one new route serving an
   existing document) but must be coordinated.

5. **DNS resolution from the DP node** — vmauth must resolve
   `core.local.genesis-core.tech` to reach the Core API for JWKS (Variant I)
   or OIDC discovery (Variant III). This is the same platform-level DNS
   assumption made by [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md).

6. **Relationship to [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md).**
   That doc covers authenticating *users to the Grafana UI* via Exordos IAM
   OIDC. This doc covers authenticating *Grafana (or any reader) to the
   Victoria data endpoints*. They are complementary axes of defense in depth:
   - user → Grafana (OIDC login, `DESIGN-grafana-oidc.md`)
   - Grafana → Victoria (vmauth + IAM token, this doc)
   Neither substitutes for the other. Full per-user access control at the
   data layer (Phase C) requires both: Grafana OIDC login + datasource
   forward-identity + vmauth per-user JWT validation.

7. **Future Phase B — write-path protection.** The write path
   (`/write`, `/insert/*`) stays open by design in this doc. A future Phase B
   would close it, either by giving base-image `vmagent`/`vlagent` a
   credential (platform-wide change touching `exordos_core` / base image
   tooling) or by tightening the `agent-writer` vmauth user to require a
   token. Native `-httpAuth.username/password` (Option D) becomes viable at
   that point because the write path is no longer required to be open.

8. **Future Phase C — per-user token forwarding + TLS.** Once
   [`DESIGN-grafana-oidc.md`](DESIGN-grafana-oidc.md) is implemented and
   Grafana forwards the logged-in user's OIDC token to the datasource,
   vmauth can validate per-user JWTs and enforce per-user RBAC via
   `match_claims`. TLS termination at vmauth closes the cleartext-credential
   gap on the wire. This is the end-state for defense in depth.

9. **The JWT/IAM variants (Variant I, Variant III) have not been tested
   against a live stand.** The vmauth config syntax, the OIDC Discovery
   incompatibility, and the Variant I JWKS polling are all based on upstream
   documentation and source reading. The future implementation must verify
   against the live stand described in `docs/DESIGN.md` (core `10.20.0.2`,
   metapaas-cp `10.20.0.20`, grafana `10.20.0.21`, victoria `10.20.0.22`).
   The implemented Basic Auth variant is covered by unit tests
   (`test_vmauth.py`) and was verified on the live stand.
