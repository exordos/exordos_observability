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

# Path on the dataplane node where the control plane delivers the Grafana
# environment config (admin user bootstrap, port) — must match the
# grafana-server systemd unit's EnvironmentFile in the dataplane image.
GRAFANA_ENV_FILE = "/etc/exordos_metapaas/grafana.env"

# Path where the paas-layer driver renders the declarative datasources
# provisioning document; Grafana reloads it via the HTTP admin API.
GRAFANA_DATASOURCES_PROVISIONING_FILE = (
    "/etc/grafana/provisioning/datasources/exordos.yaml"
)

# Path where the paas-layer driver renders the declarative dashboards
# provisioning document; Grafana reloads it via the HTTP admin API.
GRAFANA_DASHBOARDS_PROVISIONING_FILE = (
    "/etc/grafana/provisioning/dashboards/exordos.yaml"
)

# Base directory where dashboard JSON files are written, one subdirectory
# per Grafana folder (root folder uses "root").
GRAFANA_DASHBOARDS_DIR = "/var/lib/grafana/dashboards/exordos"

GRAFANA_HTTP_PORT = 3000

# Mount point of the persistent data disk on the dataplane node. Must match
# PERSISTENT_MOUNT in lib_bootstrap.sh: grafana_dp_bootstrap.sh mounts the
# disk there and bind-mounts /var/lib/grafana (and /var/log) onto it, so
# Grafana's state survives root-disk re-imaging on DP image updates.
GRAFANA_PERSISTENT_MOUNT = "/persist"

DEFAULT_DATA_DISK_SIZE = 10

GRAFANA_SLUG = "grafana"

# Durable marker file written when provisioning files have been updated
# on disk but Grafana's HTTP reload API failed. Its presence forces
# ``restore_from_dp`` to report an empty datasources dict so the
# reconciliation loop sees a diff and retries ``dump_to_dp`` (and thus
# the reload) on the next tick. Removed once the reload succeeds.
GRAFANA_RELOAD_PENDING_FILE = "/var/lib/grafana/exordos_reload_pending"

# Durable file holding the Grafana admin password for OIDC-only
# deployments. Generated once by the DP bootstrap script so the
# password is stable across reconciliation ticks and survives OIDC
# client_secret rotation (GF_SECURITY_ADMIN_PASSWORD only applies on
# first admin-user creation, so changing it later has no effect).
GRAFANA_ADMIN_PASSWORD_FILE = "/var/lib/grafana/exordos_admin_password"
