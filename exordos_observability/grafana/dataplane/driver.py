#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from __future__ import annotations

import base64
import grp
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request

import yaml
from gcl_sdk.agents.universal import constants as c
from gcl_sdk.agents.universal.drivers import exceptions as driver_exc
from gcl_sdk.agents.universal.drivers import meta
from gcl_sdk.infra import constants as pc
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types

from exordos_observability.grafana import constants

LOG = logging.getLogger(__name__)

PROVISIONING_API_VERSION = 1

# Subdirectory name used for the root folder (empty string folder).
ROOT_FOLDER_DIR = "root"


class ProvisioningReloadError(driver_exc.AgentDriverException):
    """Raised when Grafana's provisioning reload API fails.

    Carried as a marker so ``dump_to_dp`` can write a durable
    ``reload-pending`` file and re-raise, forcing the reconciliation
    loop to retry the reload on the next tick instead of silently
    accepting stale on-disk files as the applied state.
    """

    __template__ = "Grafana provisioning reload failed: {detail}"
    detail: str


def _write_file_atomic(
    path: str,
    content: str,
    mode: int = 0o640,
    group: str | None = None,
) -> bool:
    """Write file; return True if content changed.

    When ``group`` is given, the file is chowned to ``root:<group>`` so
    services running as a non-root user (e.g. Grafana) can read it
    without making it world-readable.
    """
    try:
        with open(path, "r") as f:
            existing = f.read()
        if existing == content:
            return False
    except FileNotFoundError:
        pass

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, path)
    os.chmod(path, mode)
    if group is not None:
        try:
            gid = grp.getgrnam(group).gr_gid
            os.chown(path, 0, gid)
        except (KeyError, PermissionError):
            LOG.warning("Could not chown %s to group '%s'", path, group)
    return True


def _read_grafana_admin_credentials() -> tuple[str, str]:
    """Read admin user and password for the Grafana reload API.

    For OIDC-only deployments the admin password is generated once by
    the DP bootstrap script and stored durably in
    ``GRAFANA_ADMIN_PASSWORD_FILE`` (it is NOT delivered via
    ``GF_SECURITY_ADMIN_PASSWORD`` because Grafana only applies that
    env var on first admin-user creation, so rotating the OIDC
    client_secret would change the env var but leave the DB password
    stale — causing the reload API to return 401).

    For password-auth deployments the admin password is delivered by
    the control plane via ``GF_SECURITY_ADMIN_PASSWORD`` in the env
    file, so we read it from there.
    """
    user = "admin"
    password = "admin"
    # Prefer the durable admin password file (OIDC deployments).
    durable_pw_found = False
    try:
        with open(constants.GRAFANA_ADMIN_PASSWORD_FILE) as f:
            password = f.read().strip()
        durable_pw_found = True
    except FileNotFoundError:
        pass
    # Read the admin user (and fallback password) from the env file.
    try:
        with open(constants.GRAFANA_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GF_SECURITY_ADMIN_USER="):
                    user = line.split("=", 1)[1]
                elif line.startswith("GF_SECURITY_ADMIN_PASSWORD="):
                    # Only use the env-file password when the durable
                    # file doesn't exist (password-auth deployments).
                    if not durable_pw_found:
                        password = line.split("=", 1)[1]
    except FileNotFoundError:
        LOG.warning(
            "Grafana env file %s not found, using default credentials",
            constants.GRAFANA_ENV_FILE,
        )
    return user, password


def _reload_is_pending() -> bool:
    """Check whether a provisioning reload is still pending."""
    return os.path.isfile(constants.GRAFANA_RELOAD_PENDING_FILE)


def _write_reload_pending_marker() -> None:
    """Write the durable reload-pending marker file."""
    os.makedirs(os.path.dirname(constants.GRAFANA_RELOAD_PENDING_FILE), exist_ok=True)
    with open(constants.GRAFANA_RELOAD_PENDING_FILE, "w") as f:
        f.write("pending\n")


def _clear_reload_pending_marker() -> None:
    """Remove the durable reload-pending marker file if it exists."""
    try:
        os.remove(constants.GRAFANA_RELOAD_PENDING_FILE)
    except FileNotFoundError:
        pass


def _reload_grafana_provisioning() -> None:
    """Trigger Grafana provisioning reload via HTTP API.

    Grafana 13+ does not re-provision on SIGHUP; the HTTP admin API
    endpoints must be used instead.

    Raises :class:`ProvisioningReloadError` on any HTTP/URL failure so
    that ``dump_to_dp`` can mark the reload as pending and propagate
    the error — the reconciliation loop will retry on the next tick
    instead of silently accepting stale on-disk files as applied.
    """
    user, password = _read_grafana_admin_credentials()
    base = f"http://localhost:{constants.GRAFANA_HTTP_PORT}"
    endpoints = [
        "/api/admin/provisioning/datasources/reload",
        "/api/admin/provisioning/dashboards/reload",
    ]
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    for path in endpoints:
        url = base + path
        req = urllib.request.Request(url, method="POST")
        req.add_header("Authorization", f"Basic {auth}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                LOG.info(
                    "Grafana provisioning reload %s: HTTP %s",
                    path,
                    resp.status,
                )
        except urllib.error.HTTPError as e:
            raise ProvisioningReloadError(
                detail=f"{path} failed: HTTP {e.code} {e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise ProvisioningReloadError(detail=f"{path} failed: {e.reason}") from e


def _render_provisioning_yaml(datasources: dict) -> str:
    entries = []
    for ds_uuid, ds in datasources.items():
        entry = {
            # ``uid`` is Grafana's stable datasource identifier. We
            # store the control-plane datasource UUID here so that
            # ``restore_from_dp`` can rebuild the dict keyed by the
            # same UUID as the target — otherwise the reconciliation
            # loop sees a perpetual diff (target keyed by UUID,
            # actual keyed by name) and re-runs dump_to_dp every tick.
            "uid": ds_uuid,
            "name": ds["name"],
            "type": ds["type"],
            "access": "proxy",
            "url": ds["url"],
            "isDefault": bool(ds.get("is_default", False)),
            "editable": False,
        }
        # Auth is a nested dict with ``kind`` plus kind-specific fields.
        auth = ds.get("auth")
        if auth and auth.get("kind") == "basic":
            entry["basicAuth"] = True
            entry["basicAuthUser"] = auth.get("username", "")
            entry["secureJsonData"] = {
                "basicAuthPassword": auth.get("password", ""),
            }
        entries.append(entry)
    # Sort for a stable, diff-friendly rendering (dict iteration order is
    # otherwise the DB read order, which is not guaranteed stable).
    entries.sort(key=lambda e: e["name"])
    document = {"apiVersion": PROVISIONING_API_VERSION, "datasources": entries}
    return yaml.safe_dump(document, sort_keys=False)


def _parse_provisioning_yaml(path: str) -> dict:
    try:
        with open(path) as f:
            document = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

    result = {}
    for entry in document.get("datasources") or []:
        # Prefer the stable ``uid`` (control-plane datasource UUID) as
        # the dict key so the restored state matches the target state.
        # Fall back to ``name`` for files written before ``uid`` was
        # emitted, so a rolling upgrade doesn't drop existing
        # datasources on the first restore.
        ds_uuid = entry.get("uid") or entry.get("name")
        if not ds_uuid:
            continue
        ds = {
            "name": entry.get("name", ""),
            "type": entry.get("type", ""),
            "url": entry.get("url", ""),
            "is_default": bool(entry.get("isDefault", False)),
        }
        # Reconstruct the auth dict from the rendered YAML.
        if entry.get("basicAuth"):
            secure = entry.get("secureJsonData") or {}
            ds["auth"] = {
                "kind": "basic",
                "username": entry.get("basicAuthUser", ""),
                "password": secure.get("basicAuthPassword", ""),
            }
        result[ds_uuid] = ds
    return result


def _folder_to_safe_name(folder: str) -> str:
    """Map a Grafana folder name to a filesystem-safe, collision-free name.

    The returned value is used only for two internal, non-user-facing
    purposes: the on-disk subdirectory under ``GRAFANA_DASHBOARDS_DIR``
    and the Grafana provisioning provider ``name``. The human-readable
    folder title shown in the Grafana UI is carried verbatim by the
    ``folder`` field of the provisioning YAML and is NOT derived from
    this value.

    A short hash (first 16 hex chars of SHA-1) makes the mapping
    one-to-one, so folders that differ only by characters that would
    otherwise collapse (``"team ops"`` vs ``"team_ops"``, ``"a/b"``
    vs ``"a_b"``) get distinct directories and provider names. The
    ``f`` prefix keeps the name from starting with a digit or a dot,
    and the hex alphabet contains no path separators or ``..``,
    so directory traversal outside
    ``GRAFANA_DASHBOARDS_DIR`` is impossible by construction.
    """
    if not folder:
        return ROOT_FOLDER_DIR
    digest = hashlib.sha1(folder.encode("utf-8")).hexdigest()[:16]
    return f"f{digest}"


def _render_dashboard_provisioning_yaml(dashboards: dict) -> str:
    """Render the Grafana dashboard provisioning YAML.

    Each unique folder gets its own provider entry pointing to a
    subdirectory under ``GRAFANA_DASHBOARDS_DIR``.
    """
    folders = {}
    for db in dashboards.values():
        folder = db.get("folder", "")
        folders.setdefault(folder, set())
        folders[folder].add(str(db.get("name", "")))

    providers = []
    for folder in sorted(folders):
        dirname = _folder_to_safe_name(folder)
        providers.append(
            {
                "name": f"exordos-{dirname}",
                "orgId": 1,
                "folder": folder,
                "type": "file",
                "disableDeletion": False,
                "allowUiUpdates": False,
                "options": {
                    "path": os.path.join(constants.GRAFANA_DASHBOARDS_DIR, dirname),
                },
            }
        )

    document = {"apiVersion": PROVISIONING_API_VERSION, "providers": providers}
    return yaml.safe_dump(document, sort_keys=False)


def _write_dashboard_files(base_dir: str, dashboards: dict) -> bool:
    """Write dashboard JSON files to per-folder subdirectories.

    Returns True if any file was created or changed. Also removes stale
    JSON files that are no longer part of the target set.
    """
    changed = False
    written_paths = set()

    for db_uuid, db in dashboards.items():
        folder = db.get("folder", "")
        content = db.get("content")
        if content is None:
            continue
        dirname = _folder_to_safe_name(folder)
        dir_path = os.path.join(base_dir, dirname)
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, f"{db_uuid}.json")
        json_str = json.dumps(content, sort_keys=True, indent=2)
        if _write_file_atomic(file_path, json_str, group="grafana"):
            changed = True
        written_paths.add(file_path)

    # Remove stale JSON files in all known subdirectories.
    if os.path.isdir(base_dir):
        for entry in os.listdir(base_dir):
            sub = os.path.join(base_dir, entry)
            if not os.path.isdir(sub):
                continue
            for fname in os.listdir(sub):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(sub, fname)
                if fpath not in written_paths:
                    os.remove(fpath)
                    changed = True

    return changed


def _verify_dashboard_files(base_dir: str, dashboards: dict) -> None:
    """Remove dashboards whose JSON files are missing from the dataplane.

    Does NOT read the JSON content — Grafana may modify the files after
    import (adding ``id``, ``version``, ``uid``, etc.). We only check
    file presence and drop missing entries from ``dashboards`` so the
    reconciliation loop sees a diff and re-writes them via
    ``dump_to_dp()``.
    """
    missing = []
    for db_uuid, db in dashboards.items():
        folder = db.get("folder", "")
        if db.get("content") is None:
            continue
        dirname = _folder_to_safe_name(folder)
        file_path = os.path.join(base_dir, dirname, f"{db_uuid}.json")
        if not os.path.isfile(file_path):
            LOG.warning(
                "Dashboard JSON file %s is missing from the dataplane",
                file_path,
            )
            missing.append(db_uuid)

    for db_uuid in missing:
        del dashboards[db_uuid]


class GrafanaInstance(meta.MetaDataPlaneModel):
    """Data plane model for a single Grafana node.

    Reconciles the target ``datasources`` and ``dashboards`` dicts
    (assembled control-plane side from child resources) with Grafana's
    file-based provisioning. Datasources are written to a single YAML
    document; dashboards are written as individual JSON files in
    per-folder subdirectories plus a provisioning YAML that tells Grafana
    where to find them. The on-disk files are the durable state, so
    ``restore_from_dp`` reading them back and ``dump_to_dp`` reloading
    only on content change is sufficient for idempotency.
    """

    name = properties.property(
        ra_types.String(min_length=1, max_length=512),
        required=True,
    )
    datasources = properties.property(ra_types.Dict(), default=dict)
    dashboards = properties.property(ra_types.Dict(), default=dict)
    status = properties.property(
        ra_types.Enum([s.value for s in pc.InstanceStatus]),
        default=pc.InstanceStatus.ACTIVE.value,
    )

    _meta_fields = {"uuid", "name", "dashboards"}

    def get_meta_model_fields(self) -> set[str] | None:
        return self._meta_fields

    def dump_to_dp(self) -> None:
        ds_changed = _write_file_atomic(
            constants.GRAFANA_DATASOURCES_PROVISIONING_FILE,
            _render_provisioning_yaml(self.datasources),
            group="grafana",
        )
        db_changed = _write_dashboard_files(
            constants.GRAFANA_DASHBOARDS_DIR, self.dashboards
        )
        db_yaml_changed = _write_file_atomic(
            constants.GRAFANA_DASHBOARDS_PROVISIONING_FILE,
            _render_dashboard_provisioning_yaml(self.dashboards),
            group="grafana",
        )
        # Reload is part of the apply transaction: trigger it when
        # files changed OR when a previous reload is still pending
        # (the on-disk files may already be byte-identical, but Grafana
        # never successfully reloaded them).
        if ds_changed or db_changed or db_yaml_changed or _reload_is_pending():
            try:
                _reload_grafana_provisioning()
                _clear_reload_pending_marker()
            except ProvisioningReloadError:
                # Persist the marker so the next reconciliation tick
                # retries the reload even though the files won't diff.
                _write_reload_pending_marker()
                raise

    def restore_from_dp(self) -> None:
        self.datasources = _parse_provisioning_yaml(
            constants.GRAFANA_DATASOURCES_PROVISIONING_FILE
        )
        # If a reload is still pending, the on-disk files may be
        # up-to-date but Grafana hasn't applied them yet. Report an
        # empty datasources dict so the reconciliation loop sees a diff
        # and retries dump_to_dp (which retries the reload). Once the
        # reload succeeds the marker is cleared and restore_from_dp
        # returns the real on-disk state again.
        if _reload_is_pending():
            self.datasources = {}
        # dashboards is a meta field — restored from the meta file, not
        # from the on-disk JSON files (Grafana may modify them after
        # import). We only verify that the provisioning YAML and the
        # expected JSON files still exist on disk.
        _verify_dashboard_files(constants.GRAFANA_DASHBOARDS_DIR, self.dashboards)

    def delete_from_dp(self) -> None:
        # Instance exists along with the VM — nothing to delete
        pass

    def update_on_dp(self) -> None:
        self.dump_to_dp()


class GrafanaCapabilityDriver(meta.MetaFileStorageAgentDriver):
    """Grafana capability driver for the universal agent."""

    GRAFANA_META_PATH = os.path.join(c.WORK_DIR, "grafana_meta.json")

    __model_map__ = {
        "grafana_instance_node": GrafanaInstance,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, meta_file=self.GRAFANA_META_PATH, **kwargs)
