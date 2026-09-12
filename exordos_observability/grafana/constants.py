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

GRAFANA_SLUG = "grafana"
