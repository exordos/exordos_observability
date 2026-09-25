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


# Install packages + Grafana from a pinned upstream .deb
sudo apt update
sudo apt dist-upgrade -y
sudo apt install -y libev-dev wget

# Pin the Grafana version so the DP image is reproducible and matches the
# version catalog entry name in exordos/manifests/grafanaaas.yaml.j2.
#
# The .deb is fetched directly and checked against a pinned SHA-256 instead of
# going through the Grafana APT repository: that needs the repo signing key,
# and both of its sources are unreliable (apt.grafana.com is blocked in some
# regions, keyserver.ubuntu.com intermittently answers "No data"). The Yandex
# mirror of the APT pool and dl.grafana.com ship different builds of the same
# version, so each source has its own checksum. Bump all of these together.
GRAFANA_VERSION="13.1.1"
GRAFANA_SOURCES=(
    "https://mirror.yandex.ru/mirrors/packages.grafana.com/oss/deb/pool/main/g/grafana/grafana_${GRAFANA_VERSION}_29761037902_linux_amd64.deb f6b7ffa4cb7680820d3b75e842febf99b828248a7ac8a6923c726c03846e9ded"
    "https://dl.grafana.com/oss/release/grafana_${GRAFANA_VERSION}_amd64.deb cbba39fe9580842e1742bbf5e256b6be093916345024e0206d7c5f32b56a3e62"
)
GRAFANA_DEB="/tmp/grafana_${GRAFANA_VERSION}_amd64.deb"
fetched=""
for attempt in 1 2 3; do
    for source in "${GRAFANA_SOURCES[@]}"; do
        read -r url sha256 <<< "$source"
        if wget -q -O "$GRAFANA_DEB" "$url" \
            && echo "${sha256}  ${GRAFANA_DEB}" | sha256sum -c -; then
            fetched=1
            break 2
        fi
    done
    sleep $((attempt * 10))
done
if [ -z "$fetched" ]; then
    echo "Failed to fetch Grafana ${GRAFANA_VERSION}" >&2
    exit 1
fi
sudo apt install -y "$GRAFANA_DEB"
rm -f "$GRAFANA_DEB"

# Install the VictoriaLogs datasource plugin so Grafana can query
# VictoriaLogs through its native LogsQL API instead of the incompatible
# Loki API. Plugins go outside /var/lib/grafana: that directory is moved to
# the persistent data disk at bootstrap and reused across image updates, so
# plugins kept there would never pick up the versions shipped in a new
# image. Must match paths.plugins in exordos-metapaas-grafana.service.
GRAFANA_PLUGINS_DIR="/opt/grafana/plugins"
sudo mkdir -p "$GRAFANA_PLUGINS_DIR"
sudo grafana cli --pluginsDir "$GRAFANA_PLUGINS_DIR" plugins install victoriametrics-logs-datasource
sudo chown -R grafana:grafana "$GRAFANA_PLUGINS_DIR"

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
