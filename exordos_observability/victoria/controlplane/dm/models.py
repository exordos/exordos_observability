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
from restalchemy.dm import models, properties, types, types_dynamic
from restalchemy.storage.sql import orm

from exordos_observability.common.version_ref import build_version_ref
from exordos_observability.victoria import constants as c
from exordos_observability.victoria.controlplane.dm import auth as auth_kinds


class VictoriaStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class VictoriaVersion(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "victoria_versions"

    image = properties.property(types.String(max_length=2048))
    version_ref = properties.property(
        types.String(min_length=1, max_length=4096),
    )

    def insert(self, session=None):
        self.version_ref = build_version_ref(
            c.VICTORIA_SLUG, str(self.uuid), self.image
        )
        super().insert(session=session)

    def update(self, session=None, force=False):
        self.version_ref = build_version_ref(
            c.VICTORIA_SLUG, str(self.uuid), self.image
        )
        super().update(session=session, force=force)


class VictoriaInstance(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "victoria_instances"

    name = properties.property(types.String(min_length=1, max_length=255))
    status = properties.property(
        types.Enum([status.value for status in VictoriaStatus]),
        default=VictoriaStatus.NEW.value,
    )
    ipsv4 = properties.property(
        types.TypedList(types.String(max_length=15)),
        default=list,
    )
    cpu = properties.property(types.Integer(min_value=1, max_value=128))
    ram = properties.property(types.Integer(min_value=512, max_value=1024**3))
    metrics_disk_size = properties.property(
        types.Integer(min_value=8, max_value=1024**3)
    )
    logs_disk_size = properties.property(types.Integer(min_value=8, max_value=1024**3))
    retention_period = properties.property(
        types.String(min_length=2, max_length=16),
        default=c.DEFAULT_RETENTION_PERIOD,
    )
    replicas = properties.property(types.Integer(min_value=1, max_value=1))
    version_ref = properties.property(
        types.String(min_length=1, max_length=4096),
        required=True,
    )

    # Populated by the infra builder from the actual NodeSet once it has a
    # node — plain scalar fields so consumer manifests can reference them
    # with ordinary `$path:field` syntax (e.g. Grafana datasource `url:`).
    metrics_endpoint = properties.property(types.String(max_length=512), default="")
    logs_endpoint = properties.property(types.String(max_length=512), default="")
    vmauth = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(auth_kinds.BasicAuth),
        ),
        required=True,
    )

    def insert(self, session=None):
        super().insert(session=session)

    def _validate_update(self, session=None):
        for field in ("metrics_disk_size", "logs_disk_size"):
            prop = self.properties[field]
            if prop.is_dirty() and prop.old_value > getattr(self, field):
                raise ValueError(f"{field} shrink is not supported yet")

    def update(self, session=None, force=False):
        self._validate_update(session=session)
        super().update(session=session, force=force)
