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

import abc
import logging
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as sdk_c
from gcl_sdk.infra.dm import models as sdk_models
from gcl_sdk.infra.services import builder
from restalchemy.dm import filters as dm_filters

from exordos_observability.common.client import create_core_client

LOG = logging.getLogger(__name__)

NODE_SET_KIND = sdk_models.NodeSet.get_resource_kind()


class ObservabilityInfraBuilder(builder.CoreInfraBuilder, abc.ABC):
    """Base infra builder for observability plugins.

    Encapsulates the common reconciliation flow shared by the Victoria
    and Grafana builders. Subclasses implement the plugin-specific hooks
    for disk specs, endpoint URLs, config rendering, and replicas.
    """

    def __init__(
        self,
        core_username: str,
        core_password: str,
        core_api_base_url: str,
        project_id: sys_uuid.UUID,
        instance_model: type[ua_models.InstanceWithDerivativesMixin],
    ):
        super().__init__(instance_model)
        self._project_id = project_id
        self._cclient = create_core_client(
            core_username=core_username,
            core_password=core_password,
            core_api_base_url=core_api_base_url,
            project_id=project_id,
        )

    # -- Plugin-specific hooks (must be implemented by subclasses) --

    @abc.abstractmethod
    def _build_disk_spec(
        self, instance: ua_models.InstanceWithDerivativesMixin
    ) -> sdk_models.SetDisksSpec:
        """Return the disk spec for the instance's NodeSet."""

    @abc.abstractmethod
    def _get_replicas(self, instance: ua_models.InstanceWithDerivativesMixin) -> int:
        """Return the number of replicas for the instance's NodeSet."""

    @abc.abstractmethod
    def _set_endpoints(
        self,
        instance: ua_models.InstanceWithDerivativesMixin,
        node_ips: list[str],
    ) -> None:
        """Populate instance endpoint fields from node IPs."""

    # -- Common implementation --

    def create_infra(
        self, instance: ua_models.InstanceWithDerivativesMixin
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        return self.actualize_infra(instance, builder.InfraCollection(infra_objects=()))

    def actualize_infra(
        self,
        instance: ua_models.InstanceWithDerivativesMixin,
        infra: builder.InfraCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        nodeset_target = None
        nodeset_actual = None

        for target, actual in infra.infra_objects:
            if target.get_resource_kind() == NODE_SET_KIND:
                nodeset_target = target
                nodeset_actual = actual

        # Bootstrap: no NodeSet target yet — create it from instance spec
        if nodeset_target is None:
            for obj in instance.get_infra(self._project_id):
                if obj.get_resource_kind() == NODE_SET_KIND:
                    nodeset_target = obj
                    break
            instance.status = sdk_c.InstanceStatus.IN_PROGRESS.value
            return (nodeset_target,) if nodeset_target is not None else ()

        # Keep NodeSet target in sync with current instance spec
        nodeset_target.cores = instance.cpu
        nodeset_target.ram = instance.ram
        nodeset_target.disk_spec = self._build_disk_spec(instance)
        nodeset_target.replicas = self._get_replicas(instance)

        # Actual NodeSet not yet provisioned
        if nodeset_actual is None:
            instance.status = sdk_c.InstanceStatus.IN_PROGRESS.value
            return (nodeset_target,)

        if nodeset_actual.nodes:
            node_ips = [node.get("ipv4") for node in nodeset_actual.nodes.values()]
            if not all(node_ips):
                LOG.info(
                    "Nodeset %s for instance %s is provisioned but node "
                    "IPv4 addresses are not yet assigned, waiting",
                    nodeset_actual.uuid,
                    instance.uuid,
                )
                instance.status = sdk_c.InstanceStatus.IN_PROGRESS.value
                return (nodeset_target,)
            instance.ipsv4 = node_ips
            self._set_endpoints(instance, node_ips)

        self._sync_node_private_keys(nodeset_actual)

        # Recreate configs for each node
        new_configs = []
        for node_uuid_str in nodeset_actual.nodes:
            new_configs.extend(
                instance.create_configs(sys_uuid.UUID(node_uuid_str), self._project_id)
            )

        self._set_instance_status(instance, nodeset_actual)

        return (nodeset_target, *new_configs)

    def _sync_node_private_keys(self, nodeset_actual) -> None:
        """Sync private keys for DP nodes into local DB."""
        node_keys = self._cclient.do_action(
            "/v1/compute/sets/", "get_private_keys", nodeset_actual.uuid
        )
        for u, v in node_keys.items():
            if nkey := ua_models.NodeEncryptionKey.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(u)}
            ):
                nkey.private_key = v
                nkey.update()
            else:
                nkey = ua_models.NodeEncryptionKey(uuid=sys_uuid.UUID(u), private_key=v)
                nkey.insert()

    @staticmethod
    def _set_instance_status(
        instance: ua_models.InstanceWithDerivativesMixin,
        nodeset_actual,
    ) -> None:
        try:
            instance.status = sdk_c.InstanceStatus(nodeset_actual.status).value
        except ValueError:
            instance.status = sdk_c.InstanceStatus.IN_PROGRESS.value

    def pre_delete_instance_resource(self, resource) -> None:
        """Clean up private keys of nodes belonging to the instance's NodeSet."""
        target_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "master": dm_filters.EQ(resource.uuid),
                "kind": dm_filters.EQ(NODE_SET_KIND),
            },
        )
        actual_resources = ua_models.Resource.objects.get_all(
            filters={
                "uuid": dm_filters.In(r.uuid for r in target_resources),
                "kind": dm_filters.EQ(NODE_SET_KIND),
            },
        )

        for ns in actual_resources:
            for key in ua_models.NodeEncryptionKey.objects.get_all(
                filters={"uuid": dm_filters.In(ns.value["nodes"].keys())}
            ):
                key.delete()
