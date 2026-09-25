#!/usr/bin/env bash

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

set -eu
set -x
set -o pipefail

source /usr/local/lib/exordos/lib_bootstrap.sh

GRAFANA_DATA_DIR="/var/lib/grafana"

# The node has two disks (root, data — declared in that order by the infra
# builder's SetDisksSpec, see controlplane/infra/dm/models.py). The root disk
# is re-imaged on every DP image update (i.e. every grafanaaas upgrade), so
# Grafana's state (sqlite db, admin password file) must live on the data
# disk: mount it at $PERSISTENT_MOUNT and bind-mount /var/lib/grafana onto
# it. On the first boot the image's /var/lib/grafana is copied there; on
# every later boot (including after re-imaging) the existing copy is reused.
# Plugins are installed outside /var/lib/grafana (see grafana_dp_install.sh)
# so they come from the current image rather than the persisted copy.
PERSISTENT_DISK=$(find_persistent_disk || true)
if [[ -z "$PERSISTENT_DISK" ]]; then
    echo "FATAL: expected a data disk for ${GRAFANA_DATA_DIR}, found none" >&2
    exit 1
fi

prepare_persistent_disk "$PERSISTENT_DISK" "$PERSISTENT_MOUNT" "xfs"
migrate_to_persistent_restart "/var/log" "${PERSISTENT_MOUNT}/var/log" "systemd-journald rsyslog"
migrate_to_persistent_stop_start "$GRAFANA_DATA_DIR" "${PERSISTENT_MOUNT}${GRAFANA_DATA_DIR}" \
    "exordos-metapaas-grafana" "grafana" "grafana"
persist_migrate_complete

# Generate a stable admin password for OIDC-only deployments.
# GF_SECURITY_ADMIN_PASSWORD only applies on first admin-user creation,
# so the control plane does NOT deliver it for OIDC instances (rotating
# the OIDC client_secret would change the env var but leave the DB
# password stale). Instead, generate a random password once, store it
# on the data disk, and set it via grafana-cli so it survives restarts,
# DP image updates and client_secret rotation. The dataplane driver reads this file to
# authenticate against the Grafana reload API.
ADMIN_PASSWORD_FILE="${GRAFANA_DATA_DIR}/exordos_admin_password"
if [[ ! -f "$ADMIN_PASSWORD_FILE" ]]; then
    sudo mkdir -p "$(dirname "$ADMIN_PASSWORD_FILE")"
    sudo chown grafana:grafana "$(dirname "$ADMIN_PASSWORD_FILE")"
    ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
    echo -n "$ADMIN_PASSWORD" | sudo tee "$ADMIN_PASSWORD_FILE" > /dev/null
    sudo chmod 0640 "$ADMIN_PASSWORD_FILE"
    sudo chown grafana:grafana "$ADMIN_PASSWORD_FILE"
    sudo grafana-cli --homepath="/usr/share/grafana" \
        --config="/etc/grafana/grafana.ini" \
        --configOverrides="cfg:default.paths.data=${GRAFANA_DATA_DIR}" \
        admin reset-admin-password "$ADMIN_PASSWORD" || true
    sudo chown grafana:grafana "${GRAFANA_DATA_DIR}/grafana.db"
    sudo chmod 0640 "${GRAFANA_DATA_DIR}/grafana.db"
fi

# grafana is enabled at install time (see grafana_dp_install.sh) but gated
# by ConditionPathExists on the env file, so it only starts once the control
# plane delivers /etc/exordos_metapaas/grafana.env (see OnReloadFunc in
# infra/dm/models.py).
sudo systemctl enable --now exordos-metapaas-grafana-agent

echo "Bootstrap completed successfully."
