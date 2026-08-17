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

import uuid as sys_uuid

from gcl_sdk.infra.dm import models as sdk_models

from exordos_observability.common.builder import ObservabilityInfraBuilder
from exordos_observability.common.version_ref import parse_disk_image
from exordos_observability.victoria import constants as c
from exordos_observability.victoria.controlplane.infra.dm import models


class CoreInfraBuilder(ObservabilityInfraBuilder):
    def __init__(
        self,
        core_username: str,
        core_password: str,
        core_api_base_url: str,
        project_id: sys_uuid.UUID,
        instance_model: type[models.VictoriaInstance] = models.VictoriaInstance,
    ):
        super().__init__(
            core_username=core_username,
            core_password=core_password,
            core_api_base_url=core_api_base_url,
            project_id=project_id,
            instance_model=instance_model,
        )

    def _build_disk_spec(
        self, instance: models.VictoriaInstance
    ) -> sdk_models.SetDisksSpec:
        return sdk_models.SetDisksSpec(
            disks=[
                {
                    "size": models.ROOT_DISK_SIZE,
                    "image": parse_disk_image(instance.version_ref),
                    "label": "root",
                },
                {
                    "size": instance.metrics_disk_size,
                    "label": "metrics",
                    "mount_point": c.VICTORIAMETRICS_DATA_DIR,
                },
                {
                    "size": instance.logs_disk_size,
                    "label": "logs",
                    "mount_point": c.VICTORIALOGS_DATA_DIR,
                },
            ]
        )

    def _get_replicas(self, instance: models.VictoriaInstance) -> int:
        return instance.replicas

    def _set_endpoints(
        self,
        instance: models.VictoriaInstance,
        node_ips: list[str],
    ) -> None:
        # Phase 1 is single-node: expose that one node's endpoints
        # directly. A future HA `distributed` kind can repoint these at
        # a load balancer / query-frontend without changing the field
        # type consumers already reference.
        # Endpoints point at vmauth (same port :8428), which proxies
        # read requests to localhost VM/VL and enforces Basic Auth.
        instance.metrics_endpoint = (
            f"http://{node_ips[0]}:{c.VICTORIAMETRICS_HTTP_PORT}"
        )
        instance.logs_endpoint = f"http://{node_ips[0]}:{c.VICTORIAMETRICS_HTTP_PORT}"
