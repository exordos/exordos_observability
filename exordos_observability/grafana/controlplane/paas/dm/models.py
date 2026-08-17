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

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as pc
from gcl_sdk.infra.dm import models as sdk_models
from restalchemy.dm import filters as ra_filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types

from exordos_observability.grafana.controlplane.dm import models


class GrafanaInstanceNode(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
):
    """Per-node derivative resource carrying declarative datasource state.

    The dataplane driver renders this into a Grafana file-provisioning
    datasources document (see dataplane/driver.py).
    """

    status = properties.property(
        ra_types.Enum([s.value for s in pc.InstanceStatus]),
        default=pc.InstanceStatus.NEW.value,
    )
    name = properties.property(ra_types.String(min_length=1, max_length=64))
    datasources = properties.property(ra_types.Dict(), default=dict)
    dashboards = properties.property(ra_types.Dict(), default=dict)

    @classmethod
    def get_resource_kind(cls) -> str:
        return "grafana_instance_node"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "name",
                "datasources",
                "dashboards",
            )
        )


class GrafanaInstance(
    models.GrafanaInstance,
    ua_models.InstanceWithDerivativesMixin,
    ua_models.DependenciesActiveReadinessMixin,
):
    __master_model__ = sdk_models.NodeSet
    __derivative_model_map__ = {
        "grafana_instance_node": GrafanaInstanceNode,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "grafana_instance"

    def get_readiness_dependencies(self) -> tp.Collection[ua_models.RI]:
        """Depend on the IaaS instance being ACTIVE.

        The PaaS derivative (grafana_instance_node) is scheduled to the
        dataplane agent, which only registers after the VM boots. The
        IaaS instance becomes ACTIVE once the compute set is provisioned,
        which is a reliable signal that the agent is (or is about to be)
        registered. Without this gate the builder tries to persist the
        derivative before the agent row exists in ua_agents, causing a
        foreign-key violation that rolls back the entire transaction
        (including any dashboard content fetched in the same iteration).
        """
        return (ua_models.RI("grafana_instance_iaas", self.uuid),)

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "name",
            )
        )

    def get_actual_nodeset(self):
        res = ua_models.Resource.objects.get_one(
            filters={
                "uuid": ra_filters.EQ(self.uuid),
                "kind": ra_filters.EQ("node_set"),
            }
        )
        return self.__master_model__.from_ua_resource(res)

    def get_datasources(self, session=None):
        return models.GrafanaDatasource.objects.get_all(
            session=session, filters={"instance": ra_filters.EQ(self)}
        )

    def get_dashboards(self, session=None):
        return models.GrafanaDashboard.objects.get_all(
            session=session, filters={"instance": ra_filters.EQ(self)}
        )
