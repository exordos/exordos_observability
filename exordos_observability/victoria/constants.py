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

# Path on the dataplane node where the control plane delivers the
# VictoriaMetrics/VictoriaLogs environment config (must match the
# victoriametrics/victorialogs systemd units' EnvironmentFile in the
# dataplane image).
VICTORIA_ENV_FILE = "/etc/exordos_metapaas/victoria.env"

# Path on the dataplane node where the control plane delivers the vmauth
# configuration (must match the vmauth systemd unit's -auth.config flag).
VMAUTH_CONFIG_FILE = "/etc/exordos_metapaas/vmauth.yaml"

VICTORIAMETRICS_DATA_DIR = "/var/lib/victoria-metrics-data"
VICTORIALOGS_DATA_DIR = "/var/lib/victoria-logs-data"

# External port: vmauth listens here (0.0.0.0:8428), accepting read
# requests (Basic Auth) and write requests (unauthorized_user) from
# Grafana and base-image agents. This is the port exposed in
# metrics_endpoint / logs_endpoint and used by datasources and vmagent.
VICTORIAMETRICS_HTTP_PORT = 8428

# Internal port: VictoriaMetrics listens on 127.0.0.1:8429, behind
# vmauth. Must differ from VICTORIAMETRICS_HTTP_PORT — if both bind
# 8428, vmauth wins the race and VM fails to start, causing vmauth to
# proxy to itself in an infinite loop.
VICTORIAMETRICS_INTERNAL_PORT = 8429

# Internal port: VictoriaLogs listens on 127.0.0.1:9428, behind vmauth.
# vmauth proxies /select/* and /insert/* here.
VICTORIALOGS_HTTP_PORT = 9428

DEFAULT_RETENTION_PERIOD = "30d"

VICTORIA_SLUG = "victoria"
