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

# Grafana is single-disk in Phase 1 (root disk only — no extra persistent
# disk); grafana's own state (sqlite db, provisioning files) lives under
# /var/lib/grafana on the root disk. Still preserve host journal logs
# across root-disk image updates the same way every other plugin does.
PERSISTENT_DISK=$(find_persistent_disk || true)
if [[ -n "$PERSISTENT_DISK" ]]; then
    prepare_persistent_disk "$PERSISTENT_DISK" "$PERSISTENT_MOUNT" "xfs"
    migrate_to_persistent_restart "/var/log" "${PERSISTENT_MOUNT}/var/log" "systemd-journald rsyslog"
    persist_migrate_complete
fi

# grafana is enabled at install time (see grafana_dp_install.sh) but gated
# by ConditionPathExists on the env file, so it only starts once the control
# plane delivers /etc/exordos_metapaas/grafana.env (see OnReloadFunc in
# infra/dm/models.py).
sudo systemctl enable --now exordos-metapaas-grafana-agent

echo "Bootstrap completed successfully."
