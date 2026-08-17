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

import hashlib
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as sdk_c
from gcl_sdk.infra.dm import models as sdk_models

from exordos_observability.common.version_ref import parse_disk_image
from exordos_observability.grafana import constants as c
from exordos_observability.grafana.controlplane.dm import auth as auth_kinds
from exordos_observability.grafana.controlplane.dm import models

ROOT_DISK_SIZE = 6

GRAFANA_CONF_TEMPLATE = """\
# Grafana node environment configuration
# Managed by Exordos Observability control plane — do not edit manually
GF_SECURITY_ADMIN_USER=admin
GF_SERVER_HTTP_PORT={http_port}{auth_config}
"""


class GrafanaInstance(models.GrafanaInstance, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        "node_set": sdk_models.NodeSet,
        "node": sdk_models.Node,
        "config": sdk_models.Config,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "grafana_instance_iaas"

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
                "root_disk_size",
                "replicas",
                "version_ref",
                "project_id",
                "auth",
            )
        )

    # Use `restart`, not `try-reload-or-restart`: at first boot grafana-server
    # is inactive (its ConditionPathExists=/etc/.../grafana.env is unmet
    # until the control plane delivers the config). `restart` starts it once
    # the condition passes, and re-applies config on every later change.
    OnReloadFunc = sdk_models.OnChangeShell(
        command="systemctl restart exordos-metapaas-grafana"
    )

    def _render_node_configs(self) -> tp.Dict[str, str]:
        """Render all config file contents for this node."""
        auth_config = ""
        if isinstance(self.auth, auth_kinds.PasswordAuth):
            auth_config = f"\nGF_SECURITY_ADMIN_PASSWORD={self.auth.password}"
        elif isinstance(self.auth, auth_kinds.OidcAuth):
            a = self.auth
            # Deterministic admin password for OIDC-only deployments.
            # Derived from the OIDC client_secret (private, not exposed via
            # API) so the password is stable across reconciliation ticks but
            # not predictable from public instance data. Nobody is expected
            # to use it for login; all users authenticate via OIDC.
            admin_password = hashlib.sha256(
                f"grafana-admin:{a.client_secret}".encode()
            ).hexdigest()[:32]
            auth_config = (
                f"\nGF_SECURITY_ADMIN_PASSWORD={admin_password}"
                f"\nGF_SERVER_ROOT_URL={a.root_url}"
                f"\nGF_AUTH_GENERIC_OAUTH_ENABLED=true"
                f"\nGF_AUTH_GENERIC_OAUTH_USE_OPENID_CONNECT=true"
                f"\nGF_AUTH_GENERIC_OAUTH_NAME=Exordos"
                f"\nGF_AUTH_GENERIC_OAUTH_CLIENT_ID={a.client_id}"
                f"\nGF_AUTH_GENERIC_OAUTH_CLIENT_SECRET={a.client_secret}"
                f"\nGF_AUTH_GENERIC_OAUTH_AUTH_URL={a.auth_url}"
                f"\nGF_AUTH_GENERIC_OAUTH_TOKEN_URL={a.token_url}"
                f"\nGF_AUTH_GENERIC_OAUTH_API_URL={a.api_url}"
                f"\nGF_AUTH_GENERIC_OAUTH_SCOPES={a.scopes}"
                f"\nGF_AUTH_GENERIC_OAUTH_ALLOW_SIGN_UP=true"
                f"\nGF_USERS_AUTO_ASSIGN_ORG_ROLE=Editor"
            )
        content = GRAFANA_CONF_TEMPLATE.format(
            http_port=c.GRAFANA_HTTP_PORT,
            auth_config=auth_config,
        )
        return {c.GRAFANA_ENV_FILE: content}

    def create_configs(
        self,
        node_uuid: sys_uuid.UUID,
        project_id: sys_uuid.UUID,
    ) -> tp.List[sdk_models.Config]:
        """Create Config resources for all config files on a node.

        Renders the config content and creates a Config resource for
        each file.
        """
        contents = self._render_node_configs()
        configs = []
        for path, content in contents.items():
            configs.append(
                sdk_models.Config(
                    uuid=sys_uuid.uuid5(self.uuid, f"config-{node_uuid}"),
                    name=str(node_uuid),
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
                    on_change=self.OnReloadFunc,
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
            name=f"grafanaaas-dp-{self.uuid}",
            cores=self.cpu,
            ram=self.ram,
            disk_spec=sdk_models.SetDisksSpec(
                disks=[
                    {
                        "size": self.root_disk_size,
                        "image": parse_disk_image(self.version_ref),
                        "label": "root",
                    },
                ]
            ),
            replicas=self.replicas,
            project_id=project_id,
            status=sdk_c.NodeStatus.NEW.value,
        )
        infra_objects.append(node_set)

        return infra_objects
