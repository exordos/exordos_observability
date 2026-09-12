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

import logging
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as pc
from gcl_sdk.paas.services import builder
from restalchemy.dm import filters as ra_filters
from restalchemy.storage import exceptions as storage_exceptions

from exordos_observability.common.client import create_core_client
from exordos_observability.grafana.controlplane.dm import models as cp_models
from exordos_observability.grafana.controlplane.paas.dm import models

LOG = logging.getLogger(__name__)
AGENT_UUID5_NAME = "grafanaaas"


class PaaSBuilder(builder.PaaSBuilder):
    @classmethod
    def agent_uuid_by_node(cls, node_uuid: sys_uuid.UUID) -> sys_uuid.UUID:
        return sys_uuid.uuid5(node_uuid, AGENT_UUID5_NAME)

    def schedule_paas_objects(
        self,
        instance: ua_models.InstanceWithDerivativesMixin,
        paas_objects: tp.Collection[ua_models.TargetResourceKindAwareMixin],
    ) -> dict[sys_uuid.UUID, tp.Collection[ua_models.TargetResourceKindAwareMixin]]:
        scheduled = {}
        for entity in paas_objects:
            scheduled[entity.uuid] = [entity]
        return scheduled


class GrafanaInstanceBuilder(PaaSBuilder):
    def __init__(
        self,
        instance_model: type[models.GrafanaInstance] = models.GrafanaInstance,
        core_username: str | None = None,
        core_password: str | None = None,
        core_api_base_url: str | None = None,
        project_id: sys_uuid.UUID | None = None,
    ):
        super().__init__(instance_model)
        # Needed to resolve UrnDashboardSource references against Core's
        # repo-artifact registry (see GrafanaArtifactDashboard). Optional so
        # existing unit tests that don't exercise dashboard content
        # resolution can keep constructing this builder without Core creds.
        self._cclient = None
        if core_api_base_url is not None:
            self._cclient = create_core_client(
                core_username=core_username,
                core_password=core_password,
                core_api_base_url=core_api_base_url,
                project_id=project_id,
            )

    def _get_datasources(self, instance: models.GrafanaInstance) -> dict:
        result = {}
        for ds in instance.get_datasources():
            if not ds.url:
                continue
            entry = {
                "name": ds.name,
                "type": ds.type,
                "url": ds.url,
                "is_default": ds.is_default,
            }
            if ds.auth is not None:
                entry["auth"] = ds.auth.to_dp_dict()
            result[str(ds.uuid)] = entry
        return result

    def _refresh_dashboard_content(self, dashboard: cp_models.GrafanaDashboard) -> None:
        """Fetch dashboard content from the referenced artifact if needed.

        ``version_ref`` format is ``{artifact_uuid}_{source_digest}``. The
        artifact uuid is the part before the first underscore.

        The source object caches its content in memory after the first
        successful ``dashboard()`` call, so repeated builder iterations
        don't re-fetch from external providers (e.g. grafana.com) and
        are resilient to transient rate-limiting (HTTP 429).

        Updates the dashboard ``status`` to reflect the resolution
        progress: ``IN_PROGRESS`` while fetching, ``ACTIVE`` on success,
        ``ERROR`` on failure (missing artifact, unparseable ref, fetch
        error).
        """
        if not dashboard.needs_content_refresh():
            return

        artifact_uuid_str = dashboard.version_ref.split("_", 1)[0]
        try:
            artifact_uuid = sys_uuid.UUID(artifact_uuid_str)
        except ValueError:
            LOG.warning(
                "Dashboard %s has unparseable version_ref %r, skipping",
                dashboard.uuid,
                dashboard.version_ref,
            )
            if dashboard.status != cp_models.GrafanaStatus.ERROR.value:
                dashboard.status = cp_models.GrafanaStatus.ERROR.value
                dashboard.update(force=True)
            return

        artifact = cp_models.GrafanaArtifactDashboard.objects.get_one_or_none(
            filters={"uuid": ra_filters.EQ(artifact_uuid)},
        )
        if artifact is None:
            LOG.warning(
                "Dashboard %s references missing artifact %s, skipping",
                dashboard.uuid,
                artifact_uuid,
            )
            if dashboard.status != cp_models.GrafanaStatus.ERROR.value:
                dashboard.status = cp_models.GrafanaStatus.ERROR.value
                dashboard.update(force=True)
            return

        # Mark as IN_PROGRESS before fetching.
        if dashboard.status != cp_models.GrafanaStatus.IN_PROGRESS.value:
            dashboard.status = cp_models.GrafanaStatus.IN_PROGRESS.value
            dashboard.update(force=True)

        try:
            content = artifact.source.dashboard(core_client=self._cclient)
        except Exception:
            LOG.exception(
                "Failed to fetch dashboard content for %s from artifact %s",
                dashboard.uuid,
                artifact_uuid,
            )
            if dashboard.status != cp_models.GrafanaStatus.ERROR.value:
                dashboard.status = cp_models.GrafanaStatus.ERROR.value
                dashboard.update(force=True)
            return

        dashboard.content = content
        dashboard.saved_version_ref = dashboard.version_ref
        dashboard.status = cp_models.GrafanaStatus.ACTIVE.value
        dashboard.update(force=True)

    def _get_dashboards(self, instance: models.GrafanaInstance) -> dict:
        dashboards = instance.get_dashboards()
        result = {}
        for db in dashboards:
            self._refresh_dashboard_content(db)
            if db.content is not None:
                result[str(db.uuid)] = {
                    "name": db.name,
                    "folder": db.folder,
                    "content": db.content,
                }
        return result

    def _build_paas_objects(
        self, instance: models.GrafanaInstance
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        try:
            nodeset = instance.get_actual_nodeset()
        except storage_exceptions.RecordNotFound:
            LOG.debug(
                "Nodeset for grafana instance %s not ready yet, skipping",
                instance.uuid,
            )
            return []

        nodes_by_idx = list(nodeset.nodes.keys())
        if not nodes_by_idx:
            return []

        # Phase 1 is always single-node.
        node_uuid = sys_uuid.UUID(nodes_by_idx[0])
        return [
            models.GrafanaInstanceNode(
                uuid=PaaSBuilder.agent_uuid_by_node(node_uuid),
                name=instance.name,
                datasources=self._get_datasources(instance),
                dashboards=self._get_dashboards(instance),
            )
        ]

    def create_paas_objects(
        self, instance: models.GrafanaInstance
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        return self._build_paas_objects(instance)

    def actualize_paas_objects(
        self,
        instance: models.GrafanaInstance,
        paas_collection: builder.PaaSCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        # If the derivative is already ACTIVE on the dataplane, the
        # datasources and dashboards have been delivered to the Grafana
        # node. Mark child resources as ACTIVE to reflect this.
        derivative_active = any(
            pair.actual is not None
            and pair.actual.status == pc.InstanceStatus.ACTIVE.value
            for pair in paas_collection.paas_objects
        )
        if derivative_active:
            self._mark_children_active(instance)
        return self._build_paas_objects(instance)

    def _mark_children_active(self, instance: models.GrafanaInstance) -> None:
        """Mark datasources and resolved dashboards as ACTIVE.

        Only dashboards with resolved content (``saved_version_ref`` matches
        ``version_ref``) are marked ACTIVE — unresolved ones stay in their
        current status (IN_PROGRESS/ERROR) until the builder resolves them.
        """
        for ds in instance.get_datasources():
            if ds.status != cp_models.GrafanaStatus.ACTIVE.value:
                ds.status = cp_models.GrafanaStatus.ACTIVE.value
                ds.update(force=True)
        for db in instance.get_dashboards():
            if db.content is None:
                continue
            if db.status != cp_models.GrafanaStatus.ACTIVE.value:
                db.status = cp_models.GrafanaStatus.ACTIVE.value
                db.update(force=True)
