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


class VictoriaDefinition(PaaSDefinition):
    """Victoria storages PaaS as a metapaas plugin.

    Control plane manages one VictoriaMetrics + VictoriaLogs single-node
    instance per ``VictoriaInstance``, each on its own three-disk node
    (root, metrics, logs). No dynamic per-instance derivative resources are
    scheduled to the dataplane in Phase 1 — configuration is delivered via
    the generic Config resource, so the paas/ layer is intentionally absent;
    only the infra/ layer (NodeSet + disks) exists here.
    """

    slug = "victoria"
    element_name = "victoriaaas"

    def get_type_route(self):
        from exordos_observability.victoria.controlplane.api import routes

        return routes.VictoriaRoute

    def get_migrations_path(self):
        return os.path.join(os.path.dirname(__file__), "migrations")

    def get_builders(self, core_username, core_password, core_api_base_url, project_id):
        from exordos_observability.victoria.controlplane.infra.dm.models import (
            VictoriaInstance as InfraVictoriaInstance,
        )
        from exordos_observability.victoria.controlplane.infra.services.builder import (
            CoreInfraBuilder,
        )
        from exordos_observability.victoria.controlplane.paas.dm.models import (
            VictoriaInstance as PaaSVictoriaInstance,
        )
        from exordos_observability.victoria.controlplane.paas.services.builder import (
            VictoriaInstanceBuilder,
        )

        return [
            CoreInfraBuilder(
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
                instance_model=InfraVictoriaInstance,
            ),
            VictoriaInstanceBuilder(instance_model=PaaSVictoriaInstance),
        ]

    def get_agent_models(self):
        return {
            "versions": "exordos_observability.victoria.controlplane.dm.models:VictoriaVersion",
            "instances": "exordos_observability.victoria.controlplane.infra.dm.models:VictoriaInstance",
        }

    def get_agent_filters(self):
        return {
            "versions": "description",
            "instances": "project_id",
        }
