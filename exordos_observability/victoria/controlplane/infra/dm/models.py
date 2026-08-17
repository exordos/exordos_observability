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

import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as sdk_c
from gcl_sdk.infra.dm import models as sdk_models

from exordos_observability.common.version_ref import parse_disk_image
from exordos_observability.victoria import constants as c
from exordos_observability.victoria.controlplane.dm import models

ROOT_DISK_SIZE = 6

VICTORIA_CONF_TEMPLATE = """\
# VictoriaMetrics/VictoriaLogs node environment configuration
# Managed by Exordos Observability control plane — do not edit manually
# VM/VL bind to 127.0.0.1 so they are only reachable via vmauth (which
# enforces read auth and leaves the write path open for agents).
# VM uses port {metrics_internal_port} internally; vmauth listens on
# {metrics_port} externally and proxies to it.
VM_RETENTION_PERIOD={retention_period}
VM_STORAGE_DATA_PATH={metrics_data_dir}
VM_HTTP_LISTEN_ADDR=127.0.0.1:{metrics_internal_port}
VL_RETENTION_PERIOD={retention_period}
VL_STORAGE_DATA_PATH={logs_data_dir}
VL_HTTP_LISTEN_ADDR=127.0.0.1:{logs_port}
"""

# vmauth configuration with separate read and write path ACLs.
#   grafana-reader — Basic Auth, read-only paths (query, series, labels,
#     select, vmui). Proxies to localhost VM (:8429) and VL (:9428) by path.
#   unauthorized_user — no auth, write-only paths (/write, /api/v1/write,
#     /insert/*). Keeps base-image vmagent/vlagent agents unchanged.
VMAUTH_CONF_TEMPLATE = """\
# vmauth configuration — managed by Exordos Observability control plane
# do not edit manually
users:
  # Read-only user for Grafana (Basic Auth).
  - username: {vmauth_user}
    password: {vmauth_password}
    url_map:
      - src_paths:
          - "/api/v1/query.*"
          - "/api/v1/series"
          - "/api/v1/label/[^/]+/values"
          - "/api/v1/labels"
          - "/prometheus/api/v1/.*"
          - "/graph"
          - "/vmui/.*"
        url_prefix: "http://127.0.0.1:{metrics_internal_port}"
      - src_paths:
          - "/select/.*"
        url_prefix: "http://127.0.0.1:{logs_port}"
# Anonymous write-only access for base-image agents (vmagent/vlagent).
unauthorized_user:
  url_map:
    - src_paths:
        - "/write"
        - "/api/v1/write"
      url_prefix: "http://127.0.0.1:{metrics_internal_port}"
    - src_paths:
        - "/insert/.*"
      url_prefix: "http://127.0.0.1:{logs_port}"
"""


class VictoriaInstance(models.VictoriaInstance, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        "node_set": sdk_models.NodeSet,
        "node": sdk_models.Node,
        "config": sdk_models.Config,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "victoria_instance_iaas"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "name",
                "cpu",
                "ram",
                "metrics_disk_size",
                "logs_disk_size",
                "retention_period",
                "replicas",
                "version_ref",
                "project_id",
                "vmauth",
            )
        )

    # Use `restart`, not `try-reload-or-restart`: at first boot both units are
    # inactive (their ConditionPathExists=/etc/.../victoria.env is unmet
    # until the control plane delivers the config). `restart` starts them
    # once the condition passes, and re-applies config on every later change.
    OnReloadFunc = sdk_models.OnChangeShell(
        command=(
            "systemctl restart "
            "exordos-metapaas-victoriametrics exordos-metapaas-victorialogs"
        )
    )

    # vmauth reload on config change — SIGHUP via systemctl reload.
    VmauthReloadFunc = sdk_models.OnChangeShell(
        command="systemctl reload-or-restart exordos-metapaas-vmauth"
    )

    def _render_vmauth_config(self) -> str:
        auth = self.vmauth
        if auth.KIND == "basic":
            return VMAUTH_CONF_TEMPLATE.format(
                vmauth_user=auth.username,
                vmauth_password=auth.password,
                metrics_internal_port=c.VICTORIAMETRICS_INTERNAL_PORT,
                logs_port=c.VICTORIALOGS_HTTP_PORT,
            )
        raise ValueError(f"Unsupported vmauth auth kind: {auth.KIND}")

    def _render_node_configs(self) -> tp.Dict[str, str]:
        """Render all config file contents for this node."""
        env_content = VICTORIA_CONF_TEMPLATE.format(
            retention_period=self.retention_period,
            metrics_data_dir=c.VICTORIAMETRICS_DATA_DIR,
            metrics_internal_port=c.VICTORIAMETRICS_INTERNAL_PORT,
            metrics_port=c.VICTORIAMETRICS_HTTP_PORT,
            logs_data_dir=c.VICTORIALOGS_DATA_DIR,
            logs_port=c.VICTORIALOGS_HTTP_PORT,
        )
        return {
            c.VICTORIA_ENV_FILE: env_content,
            c.VMAUTH_CONFIG_FILE: self._render_vmauth_config(),
        }

    def create_configs(
        self,
        node_uuid: sys_uuid.UUID,
        project_id: sys_uuid.UUID,
    ) -> tp.List[sdk_models.Config]:
        """Create Config resources for all config files on a node.

        Renders the config content and creates a Config resource for
        each file, with the appropriate on_change handler.
        """
        contents = self._render_node_configs()
        configs = []
        for path, content in contents.items():
            if path == c.VICTORIA_ENV_FILE:
                on_change = self.OnReloadFunc
                name = str(node_uuid)
                config_uuid = sys_uuid.uuid5(self.uuid, f"config-{node_uuid}")
            elif path == c.VMAUTH_CONFIG_FILE:
                on_change = self.VmauthReloadFunc
                name = f"vmauth-{node_uuid}"
                config_uuid = sys_uuid.uuid5(self.uuid, f"vmauth-{node_uuid}")
            else:
                raise ValueError(f"Unknown config path: {path}")

            configs.append(
                sdk_models.Config(
                    uuid=config_uuid,
                    name=name,
                    project_id=project_id,
                    status=sdk_c.InstanceStatus.NEW.value,
                    target=sdk_models.NodeTarget(
                        node=node_uuid,
                    ),
                    body=sdk_models.TextBodyConfig(
                        content=content,
                    ),
                    path=path,
                    owner="root",
                    group="root",
                    mode="0640",
                    on_change=on_change,
                )
            )
        return configs

    def get_infra(
        self,
        project_id: sys_uuid.UUID,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Return the infrastructure objects."""
        infra_objects = []

        node_set = sdk_models.NodeSet(
            uuid=self.uuid,
            name=f"victoriaaas-dp-{self.uuid}",
            cores=self.cpu,
            ram=self.ram,
            disk_spec=sdk_models.SetDisksSpec(
                disks=[
                    {
                        "size": ROOT_DISK_SIZE,
                        "image": parse_disk_image(self.version_ref),
                        "label": "root",
                    },
                    {
                        "size": self.metrics_disk_size,
                        "label": "metrics",
                        "mount_point": c.VICTORIAMETRICS_DATA_DIR,
                    },
                    {
                        "size": self.logs_disk_size,
                        "label": "logs",
                        "mount_point": c.VICTORIALOGS_DATA_DIR,
                    },
                ]
            ),
            replicas=self.replicas,
            project_id=project_id,
            status=sdk_c.NodeStatus.NEW.value,
        )
        infra_objects.append(node_set)

        return infra_objects
