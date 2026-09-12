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

import enum

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import models, properties, relationships, types, types_dynamic
from restalchemy.storage.sql import orm

from exordos_observability.common.version_ref import build_version_ref
from exordos_observability.grafana import constants as c
from exordos_observability.grafana import utils as u
from exordos_observability.grafana.controlplane.dm import auth as auth_kinds
from exordos_observability.grafana.controlplane.dm import datasource_auth, sources


class GrafanaStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class GrafanaDatasourceType(str, enum.Enum):
    PROMETHEUS = "prometheus"
    VICTORIA_LOGS = "victoriametrics-logs-datasource"


class GrafanaVersion(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "grafana_versions"

    image = properties.property(types.String(max_length=2048))
    version_ref = properties.property(
        types.String(min_length=1, max_length=4096),
    )

    def insert(self, session=None):
        self.version_ref = build_version_ref(c.GRAFANA_SLUG, str(self.uuid), self.image)
        super().insert(session=session)

    def update(self, session=None, force=False):
        self.version_ref = build_version_ref(c.GRAFANA_SLUG, str(self.uuid), self.image)
        super().update(session=session, force=force)


class GrafanaArtifactDashboard(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    """Top-level artifact storing a Grafana dashboard definition.

    The dashboard content is fetched via the polymorphic ``source`` field
    (an ``AbstractDashboardSource`` kind model). The computed ``version_ref``
    URN encodes the dashboard uuid and its source identifier, so consumer
    resources (``GrafanaDashboard``) can reference a specific dashboard
    version via ``$grafanaaas.types.grafana.dashboards.$x:version_ref``.
    """

    __tablename__ = "grafana_dashboards"

    name = properties.property(
        types.String(min_length=1, max_length=255), required=True
    )
    source = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(sources.UrnDashboardSource),
            types_dynamic.KindModelType(sources.RawDashboardSource),
            types_dynamic.KindModelType(sources.BundledDashboardSource),
        ),
        required=True,
    )
    version_ref = properties.property(
        types.String(min_length=1, max_length=4096),
    )

    def build_version_ref(self) -> str:
        return f"{self.uuid}_{self.source.source_digest()}"

    def insert(self, session=None):
        self.version_ref = self.build_version_ref()
        super().insert(session=session)

    def update(self, session=None, force=False):
        self.version_ref = self.build_version_ref()
        super().update(session=session, force=force)


class GrafanaInstance(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "grafana_instances"

    name = properties.property(types.String(min_length=1, max_length=255))
    status = properties.property(
        types.Enum([status.value for status in GrafanaStatus]),
        default=GrafanaStatus.NEW.value,
    )
    ipsv4 = properties.property(
        types.TypedList(types.String(max_length=15)),
        default=list,
    )
    ui_url = properties.property(types.String(max_length=512), default="")
    cpu = properties.property(types.Integer(min_value=1, max_value=128))
    ram = properties.property(types.Integer(min_value=512, max_value=1024**3))
    root_disk_size = properties.property(types.Integer(min_value=8, max_value=1024**3))
    auth = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(auth_kinds.PasswordAuth),
            types_dynamic.KindModelType(auth_kinds.OidcAuth),
        ),
        required=True,
    )
    # Phase 1: single-node only. The field is exposed so consumer manifests
    # have a stable interface, but the value is locked to 1.
    replicas = properties.property(
        types.Integer(min_value=1, max_value=1),
        default=1,
    )
    version_ref = properties.property(
        types.String(min_length=1, max_length=4096),
        required=True,
    )

    def delete(self, session=None, **kwargs):
        u.remove_nested_dm(GrafanaDatasource, "instance", self, session=session)
        u.remove_nested_dm(GrafanaDashboard, "instance", self, session=session)
        return super().delete(session=session, **kwargs)


class InstanceChildModel(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    instance = relationships.relationship(
        GrafanaInstance, required=True, read_only=True
    )
    status = properties.property(
        types.Enum([s.value for s in GrafanaStatus]),
        default=GrafanaStatus.NEW.value,
    )

    def touch_parent(self, session=None):
        # Enforce dataplane updates via parent model
        self.instance.update(force=True)

    def insert(self, session=None):
        super().insert(session=session)
        self.touch_parent(session=session)

    def update(self, session=None, force=False):
        super().update(session=session, force=force)
        self.touch_parent(session=session)

    def delete(self, session=None, **kwargs):
        res = super().delete(session=session, **kwargs)
        self.touch_parent(session=session)
        return res


class GrafanaDatasource(InstanceChildModel):
    __tablename__ = "grafana_datasources"

    name = properties.property(
        types.String(min_length=1, max_length=255), required=True
    )
    type = properties.property(
        types.Enum([t.value for t in GrafanaDatasourceType]), required=True
    )
    # Allow empty string during the transient state before the referenced
    # Victoria instance's metrics_endpoint/logs_endpoint are populated by
    # the infra builder. The manifest engine re-renders the url when the
    # referenced field changes, so the empty value is temporary.
    url = properties.property(types.String(max_length=512), required=True)
    is_default = properties.property(types.Boolean(), default=False)
    # Datasource authentication. Polymorphic kind model so future auth
    # methods (e.g. BearerToken for JWT-based auth) can be added without
    # changing the model or manifest grammar. Optional — datasources
    # without auth leave this as None.
    auth = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(datasource_auth.BasicAuth),
        ),
        default=None,
    )


class GrafanaDashboard(InstanceChildModel):
    """Dashboard binding on a Grafana instance.

    References a ``GrafanaArtifactDashboard`` via ``version_ref`` — the
    paas builder resolves it to fetch the dashboard content for
    provisioning on the dataplane. ``saved_version_ref`` and ``content``
    are managed by the builder: when ``version_ref`` changes, the builder
    fetches the dashboard content from the referenced artifact and stores
    it in ``content``, updating ``saved_version_ref`` to match.
    """

    __tablename__ = "grafana_instance_dashboards"

    name = properties.property(
        types.String(min_length=1, max_length=255), required=True
    )
    folder = properties.property(types.String(max_length=255), default="")
    version_ref = properties.property(
        types.String(min_length=1, max_length=4096), required=True
    )
    saved_version_ref = properties.property(
        types.AllowNone(types.String(min_length=1, max_length=4096)),
        default=None,
    )
    content = properties.property(
        types.AllowNone(types.Dict()),
        default=None,
    )

    def needs_content_refresh(self) -> bool:
        """True when version_ref has changed since the last content fetch."""
        return self.saved_version_ref != self.version_ref
