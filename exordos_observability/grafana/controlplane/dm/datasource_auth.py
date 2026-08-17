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
"""Authentication methods for GrafanaDatasource.

Each auth kind knows how Grafana authenticates against a datasource
URL (e.g. vmauth). Auth methods are polymorphic ``AbstractKindModel``
subclasses, so the ``auth`` field on ``GrafanaDatasource`` is declared
with ``KindModelSelectorType`` and the framework deserializes the
stored dict into the right subclass based on the ``kind`` field.

Phase A ships ``BasicAuth`` (HTTP Basic). Future kinds (e.g.
``BearerToken`` for JWT-based auth against vmauth+IAM) can be added
here without changing the model or manifest grammar.
"""

from __future__ import annotations

from restalchemy.dm import models as ra_models
from restalchemy.dm import properties, types_dynamic
from restalchemy.dm import types as ra_types


class AbstractDatasourceAuth(
    ra_models.SimpleViewMixin,
    types_dynamic.AbstractKindModel,
):
    """Abstract datasource authentication method.

    Subclasses implement a specific auth kind (basic, bearer_token, etc.).
    The ``kind`` field is auto-populated by ``AbstractKindModel`` from
    the class' ``KIND`` attribute.
    """

    def to_dp_dict(self) -> dict:
        """Return the DP representation of this auth method.

        Returns a dict with ``kind`` plus kind-specific fields. Override
        in subclasses to add fields. Returns an empty dict by default so
        datasources without auth produce no auth key.
        """
        return {"kind": self.KIND}


class BasicAuth(AbstractDatasourceAuth):
    """Datasource authentication via HTTP Basic Auth.

    Renders ``basicAuth``/``basicAuthUser``/``secureJsonData.basicAuthPassword``
    in the Grafana provisioning YAML.
    """

    KIND = "basic"

    username = properties.property(
        ra_types.String(min_length=1, max_length=128), required=True
    )
    password = properties.property(
        ra_types.String(min_length=1, max_length=256), required=True
    )

    def to_dp_dict(self) -> dict:
        return {
            "kind": self.KIND,
            "username": self.username,
            "password": self.password,
        }
