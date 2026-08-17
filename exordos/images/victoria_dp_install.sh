#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
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


GC_PATH="/opt/exordos_metapaas"
GC_CFG_DIR=/etc/exordos_metapaas
WORK_DIR="/var/lib/exordos/exordos_metapaas"
VM_DATA_DIR="/var/lib/victoria-metrics-data"
VL_DATA_DIR="/var/lib/victoria-logs-data"
VENV_PATH="$GC_PATH/.venv"
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/


# Install packages
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y \
    libev-dev tar

# Install VictoriaMetrics + VictoriaLogs + vmauth binaries from upstream releases.
# vmauth is part of the vmutils package.
VM_VERSION="1.148.0"
VL_VERSION="1.52.0"
VM_URL="https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v${VM_VERSION}/victoria-metrics-linux-amd64-v${VM_VERSION}.tar.gz"
VL_URL="https://github.com/VictoriaMetrics/VictoriaLogs/releases/download/v${VL_VERSION}/victoria-logs-linux-amd64-v${VL_VERSION}.tar.gz"
VMUTILS_URL="https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/v${VM_VERSION}/vmutils-linux-amd64-v${VM_VERSION}.tar.gz"

TMP_DIR=$(mktemp -d)
curl -fsSL -o "$TMP_DIR/victoria-metrics.tar.gz" "$VM_URL"
curl -fsSL -o "$TMP_DIR/victoria-logs.tar.gz" "$VL_URL"
curl -fsSL -o "$TMP_DIR/vmutils.tar.gz" "$VMUTILS_URL"
tar -xzf "$TMP_DIR/victoria-metrics.tar.gz" -C "$TMP_DIR"
tar -xzf "$TMP_DIR/victoria-logs.tar.gz" -C "$TMP_DIR"
tar -xzf "$TMP_DIR/vmutils.tar.gz" -C "$TMP_DIR"
VM_BIN=$(find "$TMP_DIR" -maxdepth 1 -type f -name 'victoria-metrics*' | head -n1)
VL_BIN=$(find "$TMP_DIR" -maxdepth 1 -type f -name 'victoria-logs*' | head -n1)
VMAUTH_BIN=$(find "$TMP_DIR" -maxdepth 1 -type f -name 'vmauth*' | head -n1)
sudo cp "$VM_BIN" /usr/bin/victoria-metrics
sudo cp "$VL_BIN" /usr/bin/victoria-logs
sudo cp "$VMAUTH_BIN" /usr/bin/vmauth
sudo chmod +x /usr/bin/victoria-metrics /usr/bin/victoria-logs /usr/bin/vmauth
rm -rf "$TMP_DIR"

# Create directories (data dirs get their own persistent disks mounted on
# top of these mount points by dp_bootstrap.sh on first boot)
sudo mkdir -p $GC_CFG_DIR
sudo mkdir -p $WORK_DIR
sudo mkdir -p $VM_DATA_DIR
sudo mkdir -p $VL_DATA_DIR

# Install agent config + bootstrap
sudo cp "$GC_PATH/etc/exordos_metapaas/metapaas_victoria_agent.conf" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_metapaas/logging.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/exordos/images/victoria_dp_bootstrap.sh" $BOOTSTRAP_PATH/0100-metapaas-victoria-dp-bootstrap.sh
sudo chmod +x $BOOTSTRAP_PATH/0100-metapaas-victoria-dp-bootstrap.sh

cd "$GC_PATH"
uv sync
source "$GC_PATH/.venv/bin/activate"

# Link the universal agent (loads the VictoriaCapabilityDriver via entry point)
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent" "/usr/bin/exordos-universal-agent"

deactivate

# Install Systemd service files
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-victoria-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-victoriametrics.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-victorialogs.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-vmauth.service" $SYSTEMD_SERVICE_DIR

# Enable the dataplane agent.
sudo systemctl enable exordos-metapaas-victoria-agent

# Enable the application units without --now. Their
# ConditionPathExists=/etc/exordos_metapaas/victoria.env keeps them inactive
# on first boot (the env file is delivered by the control plane later, and
# OnReloadFunc's `systemctl restart` starts them then). On every subsequent
# reboot the env file is already on disk, the condition passes, and the
# enabled units auto-start — without this they stay dead after a node
# reboot because nothing re-triggers OnReloadFunc unless the config changes.
sudo systemctl enable \
    exordos-metapaas-victoriametrics \
    exordos-metapaas-victorialogs \
    exordos-metapaas-vmauth
