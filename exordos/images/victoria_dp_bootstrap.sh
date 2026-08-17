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

VM_DATA_DIR="/var/lib/victoria-metrics-data"
VL_DATA_DIR="/var/lib/victoria-logs-data"

# --- Multi-disk discovery -------------------------------------------------
#
# This node has THREE disks (root, metrics, logs — declared in that order by
# the infra builder's SetDisksSpec, see controlplane/infra/dm/models.py).
# lib_bootstrap.sh's find_persistent_disk() only returns the FIRST non-root
# disk — every other plugin in this platform (exordos_s3, exordos_db,
# exordos_metapaas) only ever attaches one extra disk, so that has never
# needed to change. Victoria needs two, so it resolves them itself here.
#
# There is no in-guest disk-label lookup available anywhere in the platform
# today (SetDisksSpec.disks[].label/mount_point are validated CP-side but
# not surfaced to the guest) — device order is the only ordering guarantee,
# so disks are resolved positionally: first non-root disk = metrics, second
# = logs, matching the declared order in SetDisksSpec.
find_extra_persistent_disks() {
    local root_part root_disk_name
    root_part=$(findmnt -no SOURCE /)
    root_disk_name=$(lsblk -no PKNAME "$root_part" 2>/dev/null || true)
    if [[ -z "$root_disk_name" ]]; then
        root_disk_name=$(basename "$root_part")
    fi

    lsblk -dno NAME,TYPE \
        | awk '$2 == "disk" {print $1}' \
        | grep -vx "$root_disk_name" \
        | sort
}

mapfile -t EXTRA_DISKS < <(find_extra_persistent_disks)

if [[ "${#EXTRA_DISKS[@]}" -lt 2 ]]; then
    echo "FATAL: expected 2 extra disks (metrics, logs), found ${#EXTRA_DISKS[@]}" >&2
    exit 1
fi

METRICS_DISK="/dev/${EXTRA_DISKS[0]}"
LOGS_DISK="/dev/${EXTRA_DISKS[1]}"

prepare_persistent_disk "$METRICS_DISK" "$VM_DATA_DIR" "xfs"
prepare_persistent_disk "$LOGS_DISK" "$VL_DATA_DIR" "xfs"

# Preserve host journal logs across root-disk image updates. Placed on the
# metrics disk (arbitrary but deliberate: keeps host syslog separate from
# VictoriaLogs' own data path on the logs disk).
migrate_to_persistent_restart "/var/log" "${VM_DATA_DIR}/var-log" "systemd-journald rsyslog"
persist_migrate_complete

# victoriametrics/victorialogs are enabled at install time (see
# victoria_dp_install.sh) but gated by ConditionPathExists on the env file,
# so they only start once the control plane delivers
# /etc/exordos_metapaas/victoria.env (see OnReloadFunc in infra/dm/models.py).
sudo systemctl enable --now exordos-metapaas-victoria-agent

echo "Bootstrap completed successfully."
