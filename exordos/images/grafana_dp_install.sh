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
VENV_PATH="$GC_PATH/.venv"
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/


# Install packages + Grafana from the official upstream APT repository
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y \
    libev-dev apt-transport-https software-properties-common wget gpg

sudo mkdir -p /etc/apt/keyrings/
# apt.grafana.com is blocked in some regions; fetch the key from keyserver instead
gpg --keyserver keyserver.ubuntu.com --recv-keys B53AE77BADB630A683046005963FA27710458545
gpg --export --armor B53AE77BADB630A683046005963FA27710458545 \
    | sudo tee /etc/apt/keyrings/grafana.asc > /dev/null
sudo chmod 644 /etc/apt/keyrings/grafana.asc
# Use Yandex mirror for the Grafana APT repository
echo "deb [signed-by=/etc/apt/keyrings/grafana.asc] https://mirror.yandex.ru/mirrors/packages.grafana.com/oss/deb stable main" \
    | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt update

# Pin the Grafana version so the DP image is reproducible and matches the
# version catalog entry name in exordos/manifests/grafanaaas.yaml.j2.
GRAFANA_VERSION="13.1.1"
sudo apt install -y "grafana=${GRAFANA_VERSION}"

# Install the VictoriaLogs datasource plugin so Grafana can query
# VictoriaLogs through its native LogsQL API instead of the incompatible
# Loki API.
sudo grafana cli plugins install victoriametrics-logs-datasource

# The apt package ships its own grafana-server.service unit and starts
# grafana-server against the default config immediately; we run it under
# our own unit (gated on the control-plane-delivered env file) instead, so
# disable the upstream one.
sudo systemctl disable --now grafana-server || true
sudo systemctl mask grafana-server

# Empty out the default datasources provisioning dir — our own file is
# delivered there by the dataplane driver once datasources are declared.
sudo mkdir -p /etc/grafana/provisioning/datasources
sudo rm -f /etc/grafana/provisioning/datasources/*.yaml

# Create directories
sudo mkdir -p $GC_CFG_DIR
sudo mkdir -p $WORK_DIR

# Install agent config + bootstrap
sudo cp "$GC_PATH/etc/exordos_metapaas/metapaas_grafana_agent.conf" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_metapaas/logging.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/exordos/images/grafana_dp_bootstrap.sh" $BOOTSTRAP_PATH/0100-metapaas-grafana-dp-bootstrap.sh
sudo chmod +x $BOOTSTRAP_PATH/0100-metapaas-grafana-dp-bootstrap.sh

cd "$GC_PATH"
uv sync
source "$GC_PATH/.venv/bin/activate"

# Link the universal agent (loads the GrafanaCapabilityDriver via entry point)
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent" "/usr/bin/exordos-universal-agent"

deactivate

# Install Systemd service files
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-grafana-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-metapaas-grafana.service" $SYSTEMD_SERVICE_DIR

# Enable the dataplane agent.
sudo systemctl enable exordos-metapaas-grafana-agent

# Enable the application unit without --now. Its
# ConditionPathExists=/etc/exordos_metapaas/grafana.env keeps it inactive on
# first boot (the env file is delivered by the control plane later, and
# OnReloadFunc's `systemctl restart` starts it then). On every subsequent
# reboot the env file is already on disk, the condition passes, and the
# enabled unit auto-starts — without this it stays dead after a node reboot
# because nothing re-triggers OnReloadFunc unless the config changes.
sudo systemctl enable exordos-metapaas-grafana
