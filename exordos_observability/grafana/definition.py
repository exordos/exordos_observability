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

import os

from exordos_metapaas.registry import PaaSDefinition


class GrafanaDefinition(PaaSDefinition):
    """Grafana consumer PaaS as a metapaas plugin.

    Control plane manages one Grafana instance per ``GrafanaInstance``, each
    on its own single-disk node, with declaratively provisioned datasources
    (``GrafanaDatasource`` child resources) delivered to the dataplane as a
    Grafana file-provisioning YAML document.
    """

    slug = "grafana"
    element_name = "grafanaaas"

    def get_type_route(self):
        from exordos_observability.grafana.controlplane.api import routes

        return routes.GrafanaRoute

    def get_migrations_path(self):
        return os.path.join(os.path.dirname(__file__), "migrations")

    def get_builders(self, core_username, core_password, core_api_base_url, project_id):
        from exordos_observability.grafana.controlplane.infra.dm.models import (
            GrafanaInstance as InfraGrafanaInstance,
        )
        from exordos_observability.grafana.controlplane.infra.services.builder import (
            CoreInfraBuilder,
        )
        from exordos_observability.grafana.controlplane.paas.dm.models import (
            GrafanaInstance as PaaSGrafanaInstance,
        )
        from exordos_observability.grafana.controlplane.paas.services.builder import (
            GrafanaInstanceBuilder,
        )

        return [
            CoreInfraBuilder(
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
                instance_model=InfraGrafanaInstance,
            ),
            GrafanaInstanceBuilder(
                instance_model=PaaSGrafanaInstance,
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
            ),
        ]

    def get_agent_models(self):
        return {
            "versions": "exordos_observability.grafana.controlplane.dm.models:GrafanaVersion",
            "instances": "exordos_observability.grafana.controlplane.infra.dm.models:GrafanaInstance",
            "instances.datasources": "exordos_observability.grafana.controlplane.dm.models:GrafanaDatasource",
            "instances.dashboards": "exordos_observability.grafana.controlplane.dm.models:GrafanaDashboard",
            "dashboards": "exordos_observability.grafana.controlplane.dm.models:GrafanaArtifactDashboard",
        }

    def get_agent_filters(self):
        return {
            "versions": "description",
            "instances": "project_id",
            "instances.datasources": "project_id",
            "instances.dashboards": "project_id",
            "dashboards": "project_id",
        }
