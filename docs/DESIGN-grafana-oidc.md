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

# Grafana OIDC Authentication via Exordos IAM

> **Status: Implemented.** This document describes the implemented contract
> for Grafana instance authentication using Exordos Core's IAM as the
> identity provider. The approach follows the polymorphic kind-model pattern
> from dashboard sources (`controlplane/dm/sources.py`).

A polymorphic `auth` field on `GrafanaInstance` selects between two auth
kinds: `password` (`PasswordAuth`) and `oidc` (`OidcAuth`). The field is
**required** — every instance declares exactly one auth method. When `oidc`
is selected, the infra builder renders `GF_AUTH_GENERIC_OAUTH_*` env vars
and **no** admin password (the DP bootstrap generates one instead — see
[OIDC admin password](#oidc-admin-password-is-no-longer-derived-from-client_secret)).
When `password` is selected, the builder renders `GF_SECURITY_ADMIN_PASSWORD`
from the kind's `password` field. The observability composition manifest
declares a dedicated IAM client + Idp in Core and wires the Grafana instance
to use OIDC.

## Research Findings

### Feasibility: YES

1. **Grafana supports generic OAuth/OIDC** via `GF_AUTH_GENERIC_OAUTH_*` env
   vars — needs `client_id`, `client_secret`, `auth_url`, `token_url`,
   `api_url`, `scopes`.

2. **Exordos Core IAM has full OIDC provider support**:
   - `IamClient` model (`exordos_core/user_api/iam/dm/models.py:1040`) — OIDC
     client with `client_id`, `secret` (hashed), `signature_algorithm`
     (HS256/RS256).
   - `Idp` model (`exordos_core/user_api/iam/dm/models.py:1811`) — OIDC
     identity provider, linked to an IamClient, exposes
     `.well-known/openid-configuration` with `authorization_endpoint`,
     `token_endpoint`, `userinfo_endpoint`, `jwks_uri`.
   - Both are manifest-declarable: `$core.iam.clients` and `$core.iam.idp`
     exist in `full_spec.yaml`.

3. **Cannot reuse the default Core IAM client** — it's seeded via SQL
   migration (uuid `00000000-0000-0000-0000-000000000000`), not declared as
   a manifest resource. Its UUID/id/secret are stored in Core variables
   (`$core.vs.variables.$iam_default_client_*`) and *could* be imported.
   However, the secret variable (`iam_default_client_secret`) holds the
   **plaintext** default secret (`GenesisCoreSecret`) — exposing it to a
   consumer element would leak the platform-wide default client credentials.
   **Decision: create a dedicated IAM client in the observability manifest**
   with its own auto-generated secret stored in `$core.secret.passwords`.

4. **IamClient `secret` is write-only** (hashed via PBKDF2) — can't read it
   back. Solution: store the client secret in a `$core.secret.passwords`
   resource and reference its `:value` both for the IAM client creation AND
   for the Grafana OIDC config.

5. **HS256 signing secret** — the default HS256 secret (uuid
   `00000000-0000-0000-0000-000000000001`) is seeded via SQL and can be
   referenced by its well-known UUID in the `signature_algorithm` block (same
   as the commented-out example in `core.yaml.j2`).

6. **Core API URL from DP node/browser** — `core.local.genesis-core.tech`
   resolves to the core IP (A record declared in `core.yaml.j2`). The LB
   vhost accepts any host (`domains: ["_"]`) on port 80, proxying
   `/api/core/v1/` to the core process. OIDC endpoints are at
   `/api/core/v1/iam/idp/{uuid}/actions/authorize/invoke` etc.

7. **Callback URL** — `http://observability.local.genesis-core.tech/login/generic_oauth`
   (Grafana's generic OAuth callback path). The DNS A record for
   `observability` is already declared in `observability.yaml.j2`.

### OIDC Flow

1. Browser → Grafana login → "Sign in with Exordos"
2. Grafana redirects browser → `auth_url` (Core authorize endpoint)
3. User authenticates on Core (admin user exists by default)
4. Core redirects browser → `callback_url` (Grafana `/login/generic_oauth`)
5. Grafana DP node exchanges code → `token_url` (Core token endpoint)
6. Grafana DP node fetches user info → `api_url` (Core userinfo endpoint)
7. `registration_auto_provision=true` (default) auto-creates the Grafana user

## Implemented Contract

### Auth kind models — `grafana/controlplane/dm/auth.py`

`AbstractAuthMethod` (`SimpleViewMixin` + `AbstractKindModel`) is the
polymorphic base. Two concrete kinds are implemented:

- **`PasswordAuth`** (`KIND="password"`) — a single required field:
  - `password` (`String`, min 1, max 256, **required**) — rendered as
    `GF_SECURITY_ADMIN_PASSWORD` in the Grafana env file. Typically sourced
    from a Secret Manager resource in the manifest
    (`$core.secret.passwords.$...:value`).

- **`OidcAuth`** (`KIND="oidc"`) — fields:
  - `client_id` (`String`, min 1, max 128, required)
  - `client_secret` (`String`, min 1, max 256, required)
  - `auth_url` (`String`, min 1, max 2048, required) — OIDC authorization
    endpoint; browser redirects here for user login.
  - `token_url` (`String`, min 1, max 2048, required) — token endpoint;
    Grafana exchanges the auth code for an access token here.
  - `api_url` (`String`, min 1, max 2048, required) — UserInfo endpoint;
    Grafana fetches the user profile after token exchange.
  - `root_url` (`String`, min 1, max 2048, required) — public base URL
    Grafana is served at; rendered as `GF_SERVER_ROOT_URL`. Grafana derives
    its OAuth `redirect_uri` from this, so it MUST match the callback
    registered in the IAM Idp (otherwise the OIDC provider rejects with
    `InvalidRedirectUri`).
  - `scopes` (`String`, max 256, default `"openid profile email"`)

  The admin password is **not** delivered by the control plane for OIDC
  deployments — it is generated by the DP image bootstrap procedure (see
  [OIDC admin password](#oidc-admin-password-is-no-longer-derived-from-client_secret)),
  so all users authenticate through OIDC.

### `auth` field on `GrafanaInstance` — `grafana/controlplane/dm/models.py`

```python
auth = properties.property(
    types_dynamic.KindModelSelectorType(
        types_dynamic.KindModelType(auth_kinds.PasswordAuth),
        types_dynamic.KindModelType(auth_kinds.OidcAuth),
    ),
    required=True,
)
```

`auth` is **required** (`required=True`) and has **no default** — every
instance must declare an auth method explicitly. The framework
deserializes the stored dict into the right subclass based on the `kind`
field. `GrafanaInstance` has **no** `admin_password` field; the password
lives on the `PasswordAuth` kind model.

### Database schema — `grafana/migrations/0000-init-grafana.py`

The `auth` column is part of the initial schema, declared in
`0000-init-grafana.py` alongside the `grafana_instances` table:

```sql
auth JSONB NOT NULL,
```

There is **no** separate `0001-add-auth-field.py` migration — the column
has been `NOT NULL` since the initial migration.

### Infra rendering — `grafana/controlplane/infra/dm/models.py`

`GrafanaInstance._render_node_configs()` (the infra DM model, not
`infra/services/builder.py`) renders the Grafana env file
(`GRAFANA_ENV_FILE`). The base template carries only the admin user and
HTTP port; auth-specific vars are appended based on the auth kind:

```python
GRAFANA_CONF_TEMPLATE = """\
# Grafana node environment configuration
# Managed by Exordos Observability control plane — do not edit manually
GF_SECURITY_ADMIN_USER=admin
GF_SERVER_HTTP_PORT={http_port}{auth_config}
"""
```

- **`PasswordAuth`** → appends `GF_SECURITY_ADMIN_PASSWORD={password}`.
- **`OidcAuth`** → appends the OIDC block (no admin password):
  `GF_SERVER_ROOT_URL`, `GF_AUTH_GENERIC_OAUTH_ENABLED=true`,
  `GF_AUTH_GENERIC_OAUTH_USE_OPENID_CONNECT=true`,
  `GF_AUTH_GENERIC_OAUTH_NAME=Exordos`, `..._CLIENT_ID`, `..._CLIENT_SECRET`,
  `..._AUTH_URL`, `..._TOKEN_URL`, `..._API_URL`, `..._SCOPES`,
  `..._ALLOW_SIGN_UP=true`, and `GF_USERS_AUTO_ASSIGN_ORG_ROLE=Editor`.

`auth` is included in `get_resource_target_fields()`, so changes to OIDC
endpoints (e.g. when `var_core_api_url` is updated via CLI) are detected via
the `hash` comparison, not only `full_hash` — following the S3 reference
plugin pattern where all config-affecting fields are target fields. The
config is delivered with mode `0640` (root:root) and triggers
`systemctl restart exordos-metapaas-grafana` on change.

### API field permissions — `grafana/controlplane/api/controllers.py`

`auth` is `HIDDEN` in `GrafanaInstanceController` (it contains
`client_secret`/`password`), the same treatment as the datasource `auth`
field — configured via manifest, not readable via the API:

```python
"auth": {constants.ALL: field_p.Permissions.HIDDEN},
```

### Manifest wiring — `exordos/manifests/observability.yaml.j2`

The observability composition declares a dedicated IAM client + Idp and a
Secret Manager password for the client secret, then wires the Grafana
instance to OIDC:

```yaml
# Client secret — generated and stored by the Core Secret Manager as a
# URL-safe base64 string (AUTO_URL_SAFE). Referenced via $...:value, so it
# is never hardcoded in the manifest.
$core.secret.passwords:
  grafana_oidc_client_secret:
    name: "grafana_oidc_client_secret"
    description: "Grafana OIDC client secret"
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    method: "AUTO_URL_SAFE"
    constructor:
      kind: plain
    default_length: 32

# Dedicated IAM client for Grafana OIDC. Uses the platform-wide default
# HS256 signing secret (seeded via SQL migration, uuid
# 00000000-0000-0000-0000-000000000001).
$core.iam.clients:
  grafana_oidc:
    name: "Grafana OIDC Client"
    description: "IAM client for Grafana OIDC authentication"
    client_id: "grafana-oidc"
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    secret: $core.secret.passwords.$grafana_oidc_client_secret:value
    signature_algorithm:
      kind: HS256
      secret_uuid: "00000000-0000-0000-0000-000000000001"
      previous_secret_uuid: null

$core.iam.idp:
  grafana_idp:
    name: "Grafana OIDC Provider"
    description: "OIDC provider for Grafana"
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    iam_client: $core.iam.clients.$grafana_oidc:uuid
    callback:
      kind: callback_uri
      callback: "http://observability.local.genesis-core.tech/login/generic_oauth"
    # Grafana's generic OAuth provider does not send a nonce parameter in
    # the authorization request, so the Idp must not require it.
    nonce_required: false
    scope: "openid profile email"
```

The Grafana instance uses OIDC auth (no `admin_password` field on the
instance — the password lives on the auth kind):

```yaml
$grafanaaas.types.grafana.instances:
  grafana:
    name: "grafana"
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    cpu: $observability.imports.$var_default_cores:value
    ram: $observability.imports.$var_default_ram:value
    root_disk_size: 20
    replicas: 1
    auth:
      kind: oidc
      client_id: $core.iam.clients.$grafana_oidc:client_id
      client_secret: $core.secret.passwords.$grafana_oidc_client_secret:value
      auth_url: f"{$core.vs.variables.$var_core_api_url:value}/api/core/v1/iam/idp/{$core.iam.idp.$grafana_idp:uuid}/actions/authorize/invoke"
      token_url: f"{$core.vs.variables.$var_core_api_url:value}/api/core/v1/iam/clients/{$core.iam.clients.$grafana_oidc:uuid}/actions/get_token/invoke"
      api_url: f"{$core.vs.variables.$var_core_api_url:value}/api/core/v1/iam/clients/{$core.iam.clients.$grafana_oidc:uuid}/actions/userinfo"
      root_url: $core.vs.variables.$var_grafana_root_url:value
      scopes: "openid profile email"
    version_ref: $observability.imports.$grafana_v13:version_ref
```

### DP bootstrap & driver — admin password handling

For OIDC deployments the admin password is **not** delivered by the
control plane. The DP bootstrap script
(`exordos/images/grafana_dp_bootstrap.sh`) generates a random password
once, stores it durably in `GRAFANA_ADMIN_PASSWORD_FILE` (owned by
`grafana:grafana`, mode `0640`), and sets it in Grafana's SQLite DB via
`grafana-cli admin reset-admin-password`. The dataplane driver's
`_read_grafana_admin_credentials()` (`grafana/dataplane/driver.py`) prefers
this durable file over the env-file password, so the reload API credentials
stay stable across `client_secret` rotation. See
[OIDC admin password](#oidc-admin-password-is-no-longer-derived-from-client_secret)
for the rationale.

### Tests

- `grafana/tests/unit/test_models.py::TestAuthMethod` — `PasswordAuth`
  kind, `OidcAuth` deserialization, `scopes` default, and that
  `GrafanaInstance` accepts both auth kinds.
- `grafana/tests/unit/test_models.py::TestGrafanaInstance::test_auth_is_required_without_default`
  — confirms `auth` is required with no default.
- `grafana/tests/unit/test_builder.py::TestRenderNodeConfig` —
  `test_password_auth_renders_no_oidc_vars` (env file has no OAuth vars),
  `test_oidc_auth_renders_oauth_vars` (env file has all OAuth vars with
  correct values), and
  `test_oidc_env_is_stable_across_client_secret_rotation` (the durable
  admin password keeps the reload API working when the secret rotates).

## Risks / Considerations

1. **DNS resolution from DP node** — the Grafana DP node must resolve
   `core.local.genesis-core.tech` to reach the Core API for token exchange
   and userinfo fetch. This is a platform-level DNS concern; the same
   assumption is made by other cross-element integrations.

2. **Browser DNS resolution** — the user's browser must resolve both
   `core.local.genesis-core.tech` (for the OIDC redirect) and
   `observability.local.genesis-core.tech` (for the callback). This requires
   the platform DNS to be accessible from the user's network.

3. **`client_secret` stored as plaintext in DB** — same as the
   `PasswordAuth.password`. Protected by `HIDDEN` field permission in the
   API. The env file is delivered with mode `0640` (root:root).

4. **HS256 secret UUID** — the IAM client references the default HS256
   secret (uuid `00000000-0000-0000-0000-000000000001`) which is seeded via
   SQL migration. This is a well-known fixed UUID, same as the
   commented-out example in `core.yaml.j2`.

5. **`auth` in `get_resource_target_fields()`** — `auth` is included in
   the target fields so that changes to OIDC endpoints (e.g. when the
   `var_core_api_url` platform variable is updated via CLI) are detected
   via the `hash` comparison, not only `full_hash`. This follows the S3
   reference plugin pattern where all config-affecting fields (buckets,
   users, policies, access_keys) are in target fields.

6. **End-to-end OIDC browser flow** — the model contract, env-file
   rendering, and auth delivery are covered by unit tests and were
   verified on the live stand (see `docs/DESIGN.md` M10 and the 2026-08-17
   stand-verification note). The full browser redirect → token exchange
   → userinfo round trip is not exercised by the automated test suite
   (functional tests reuse the composition's instances and cannot drive a
   browser login); it has been exercised manually on the stand.

## Post-review fixes

### Provisioning reload is part of the apply transaction

Originally `_reload_grafana_provisioning()` caught `HTTPError`/`URLError`
and only logged them, so `dump_to_dp()` returned successfully even when
Grafana failed to reload its provisioning files. On the next tick the
files were byte-identical, so `*_changed=False` and the reload was never
retried — Grafana kept using the old configuration until a manual
restart.

Fix: `_reload_grafana_provisioning()` now raises `ProvisioningReloadError`
on any HTTP/URL failure. `dump_to_dp()` writes a durable marker file
(`GRAFANA_RELOAD_PENDING_FILE`) before re-raising, so:

- `restore_from_dp()` returns an empty `datasources` dict while the
  marker exists, forcing the reconciliation loop to see a diff and
  retry `dump_to_dp()` (and thus the reload) on the next tick — even
  though the on-disk files are already up-to-date.
- `dump_to_dp()` retries the reload whenever the marker exists,
  regardless of whether the files changed.
- The marker is cleared once a reload succeeds.

### OIDC admin password is no longer derived from `client_secret`

Originally the infra builder derived the Grafana admin password from
the OIDC `client_secret` via `sha256("grafana-admin:" + client_secret)`
and delivered it as `GF_SECURITY_ADMIN_PASSWORD`. But Grafana only
applies `GF_SECURITY_ADMIN_PASSWORD` on first admin-user creation —
rotating the `client_secret` would change the env var but leave the DB
password stale, causing the reload API to return 401.

Fix: the env file no longer carries `GF_SECURITY_ADMIN_PASSWORD` for
OIDC deployments. Instead, the DP bootstrap script
(`grafana_dp_bootstrap.sh`) generates a random password once, stores it
durably in `GRAFANA_ADMIN_PASSWORD_FILE` (owned by `grafana:grafana`,
mode `0640`), and sets it in Grafana's SQLite DB via `grafana-cli admin
reset-admin-password`. The dataplane driver's
`_read_grafana_admin_credentials()` prefers this durable file over the
env-file password, so the reload API credentials stay stable across
`client_secret` rotation.
