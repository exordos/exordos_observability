# Grafana OIDC Authentication via Exordos IAM

> **Status: Implemented.** This document captures the design for optional
> OIDC authentication on Grafana instances using Exordos Core's IAM as the
> identity provider. The approach follows the polymorphic kind-model pattern
> from dashboard sources (`controlplane/dm/sources.py`).

Add a polymorphic `auth` field to `GrafanaInstance` (kind: `password` |
`oidc`). When `oidc` is selected, the infra builder renders
`GF_AUTH_GENERIC_OAUTH_*` env vars alongside the existing admin password.
The observability composition manifest declares a dedicated IAM client + Idp
in Core, and wires the Grafana instance to use OIDC with the `admin` user.

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

## Implementation Plan

### 1. Create `auth.py` — auth method kind models

**New file**: `exordos_observability/grafana/controlplane/dm/auth.py`

Following the `sources.py` pattern:

- `AbstractAuthMethod` (abstract kind model, `SimpleViewMixin` +
  `AbstractKindModel`)
- `PasswordAuth` (KIND="password") — no extra fields; uses `admin_password`
  on the instance
- `OidcAuth` (KIND="oidc") — fields:
  - `client_id` (String, required, max 128)
  - `client_secret` (String, required, max 256)
  - `auth_url` (String, required, max 2048)
  - `token_url` (String, required, max 2048)
  - `api_url` (String, required, max 2048)
  - `root_url` (String, required, max 2048) — public base URL Grafana is
    served at; rendered as `GF_SERVER_ROOT_URL`. Grafana derives its OAuth
    `redirect_uri` from this, so it MUST match the callback registered in
    the IAM Idp (otherwise the OIDC provider rejects with
    `InvalidRedirectUri`).
  - `scopes` (String, max 256, default="openid profile email")

### 2. Add `auth` field to `GrafanaInstance` model

**File**: `exordos_observability/grafana/controlplane/dm/models.py`

```python
from exordos_observability.grafana.controlplane.dm import auth as auth_kinds

# In GrafanaInstance:
auth = properties.property(
    types_dynamic.KindModelSelectorType(
        types_dynamic.KindModelType(auth_kinds.PasswordAuth),
        types_dynamic.KindModelType(auth_kinds.OidcAuth),
    ),
    default=auth_kinds.PasswordAuth,
)
```

Optional (not `required=True`) with `default=PasswordAuth` — existing
instances/tests without `auth` default to password auth.

### 3. Add migration for `auth` column

**New file**: `exordos_observability/grafana/migrations/0001-add-auth-field.py`

```sql
ALTER TABLE grafana_instances ADD COLUMN auth JSONB;
```

Nullable (existing rows get NULL → model defaults to PasswordAuth). Pattern
matches `0000-init-grafana.py`.

### 4. Update infra builder to render OIDC env vars

**File**: `exordos_observability/grafana/controlplane/infra/services/builder.py`

Update `GRAFANA_CONF_TEMPLATE` and `_render_node_config`:

- When `instance.auth` is `OidcAuth`, append `GF_AUTH_GENERIC_OAUTH_*` env
  vars
- When `instance.auth` is `PasswordAuth`, render only the existing vars (no
  change)

```python
GRAFANA_CONF_TEMPLATE = """\
# Grafana node environment configuration
# Managed by Exordos Observability control plane — do not edit manually
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD={admin_password}
GF_SERVER_HTTP_PORT={http_port}{oidc_config}
"""


def _render_node_config(self, instance, node_uuid_str):
    oidc_config = ""
    if isinstance(instance.auth, auth_kinds.OidcAuth):
        a = instance.auth
        oidc_config = (
            f"\nGF_SERVER_ROOT_URL={a.root_url}"
            f"\nGF_AUTH_GENERIC_OAUTH_ENABLED=true"
            f"\nGF_AUTH_GENERIC_OAUTH_NAME=Exordos"
            f"\nGF_AUTH_GENERIC_OAUTH_CLIENT_ID={a.client_id}"
            f"\nGF_AUTH_GENERIC_OAUTH_CLIENT_SECRET={a.client_secret}"
            f"\nGF_AUTH_GENERIC_OAUTH_AUTH_URL={a.auth_url}"
            f"\nGF_AUTH_GENERIC_OAUTH_TOKEN_URL={a.token_url}"
            f"\nGF_AUTH_GENERIC_OAUTH_API_URL={a.api_url}"
            f"\nGF_AUTH_GENERIC_OAUTH_SCOPES={a.scopes}"
            f"\nGF_AUTH_GENERIC_OAUTH_ALLOW_SIGN_UP=true"
        )
    return GRAFANA_CONF_TEMPLATE.format(
        admin_password=instance.admin_password,
        http_port=c.GRAFANA_HTTP_PORT,
        oidc_config=oidc_config,
    )
```

### 5. Hide `auth` in API (contains `client_secret`)

**File**: `exordos_observability/grafana/controlplane/api/controllers.py`

Add to `GrafanaInstanceController` field permissions:

```python
"auth": {constants.ALL: field_p.Permissions.HIDDEN},
```

Same treatment as `admin_password` — configured via manifest, not readable
via API.

### 6. Declare IAM resources + wire OIDC in observability manifest

**File**: `exordos/manifests/observability.yaml.j2`

Add before the Grafana instance declaration:

```yaml
# IAM client for Grafana OIDC authentication.
# The secret is stored in the Secret Manager and referenced by both
# the IAM client (hashed) and the Grafana instance (plaintext env var).
$core.secret.passwords:
  grafana_oidc_client_secret:
    name: "grafana_oidc_client_secret"
    description: "Grafana OIDC client secret"
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    method: "AUTO_URL_SAFE"
    constructor:
      kind: plain
    default_length: 32

$core.iam.clients:
  grafana_oidc:
    name: "Grafana OIDC Client"
    description: "IAM client for Grafana OIDC authentication"
    client_id: "grafana-oidc"
    secret: $core.secret.passwords.$grafana_oidc_client_secret:value
    signature_algorithm:
      kind: HS256
      secret_uuid: "00000000-0000-0000-0000-000000000001"
      previous_secret_uuid: null

$core.iam.idp:
  grafana_idp:
    name: "Grafana OIDC Provider"
    description: "OIDC provider for Grafana"
    iam_client: $core.iam.clients.$grafana_oidc:uuid
    callback:
      kind: callback_uri
      callback: "http://observability.local.genesis-core.tech/login/generic_oauth"
    # Grafana's generic OAuth provider does not send a nonce parameter in
    # the authorization request, so the Idp must not require it.
    nonce_required: false
    scope: "openid profile email"
```

Update the Grafana instance to use OIDC auth:

```yaml
$grafanaaas.types.grafana.instances:
  grafana:
    name: "grafana"
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    cpu: $observability.imports.$var_default_cores:value
    ram: $observability.imports.$var_default_ram:value
    root_disk_size: 20
    admin_password: $core.secret.passwords.$grafana_admin_password:value
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

### 7. Add unit tests

**File**: `exordos_observability/grafana/tests/unit/test_models.py`

- `TestAuthMethod` class:
  - `test_password_auth_kind` — PasswordAuth has KIND="password", no extra
    fields
  - `test_oidc_auth_deserializes` — OidcAuth deserializes from dict with
    all fields
  - `test_auth_defaults_to_password` — GrafanaInstance without `auth`
    defaults to PasswordAuth
  - `test_auth_accepts_oidc` — GrafanaInstance with
    `auth={kind: oidc, ...}` deserializes to OidcAuth

**File**: `exordos_observability/grafana/tests/unit/test_builder.py`

- `TestRenderNodeConfig` class:
  - `test_password_auth_renders_no_oidc_vars` — env file has no
    `GF_AUTH_GENERIC_OAUTH_*`
  - `test_oidc_auth_renders_oauth_vars` — env file has all
    `GF_AUTH_GENERIC_OAUTH_*` with correct values

## Files to Modify

| File | Change |
|------|--------|
| `grafana/controlplane/dm/auth.py` | **NEW** — `AbstractAuthMethod`, `PasswordAuth`, `OidcAuth` kind models |
| `grafana/controlplane/dm/models.py` | Add `auth` property to `GrafanaInstance` |
| `grafana/migrations/0001-add-auth-field.py` | **NEW** — `ALTER TABLE grafana_instances ADD COLUMN auth JSONB` |
| `grafana/controlplane/infra/services/builder.py` | Render `GF_AUTH_GENERIC_OAUTH_*` env vars when auth is OidcAuth |
| `grafana/controlplane/api/controllers.py` | Hide `auth` field in API (contains client_secret) |
| `exordos/manifests/observability.yaml.j2` | Declare IAM client + Idp + password; wire Grafana instance auth to oidc |
| `grafana/tests/unit/test_models.py` | Tests for auth kind models + deserialization |
| `grafana/tests/unit/test_builder.py` | Tests for env file rendering with password/oidc auth |

## Verification

- [ ] `tox -e ruff-check` passes
- [ ] `tox -e py312` passes (all existing + new unit tests)
- [ ] `test_password_auth_renders_no_oidc_vars` — env file has no OAuth vars
- [ ] `test_oidc_auth_renders_oauth_vars` — env file has all OAuth vars with
      correct values
- [ ] `test_auth_defaults_to_password` — instance without auth defaults to
      PasswordAuth
- [ ] Existing tests still pass (auth is optional with
      default=PasswordAuth)

## Risks / Considerations

1. **DNS resolution from DP node** — the Grafana DP node must resolve
   `core.local.genesis-core.tech` to reach the Core API for token exchange
   and userinfo fetch. This is a platform-level DNS concern; the same
   assumption is made by other cross-element integrations.

2. **Browser DNS resolution** — the user's browser must resolve both
   `core.local.genesis-core.tech` (for the OIDC redirect) and
   `observability.local.genesis-core.tech` (for the callback). This requires
   the platform DNS to be accessible from the user's network.

3. **`client_secret` stored as plaintext in DB** — same as
   `admin_password`. Protected by `HIDDEN` field permission in the API. The
   env file is delivered with mode `0640` (root:root).

4. **HS256 secret UUID** — the IAM client references the default HS256
   secret (uuid `00000000-0000-0000-0000-000000000001`) which is seeded via
   SQL migration. This is a well-known fixed UUID, same as the commented-out
   example in `core.yaml.j2`.

5. **`auth` in `get_resource_target_fields()`** — `auth` is included in
   the target fields so that changes to OIDC endpoints (e.g. when the
   `var_core_api_url` platform variable is updated via CLI) are detected
   via the `hash` comparison, not only `full_hash`. This follows the S3
   reference plugin pattern where all config-affecting fields (buckets,
   users, policies, access_keys) are in target fields.

6. **Nothing in this repo has been tested against a live stand** — per
   `docs/DESIGN.md`, the OIDC flow (browser redirect, token exchange,
   userinfo fetch) is unverified end-to-end. Unit tests only verify model
   deserialization and env file rendering.
