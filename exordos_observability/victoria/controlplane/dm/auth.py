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
"""Authentication methods for vmauth on a Victoria instance.

Each auth kind knows how to configure vmauth's reader authentication.
Auth methods are polymorphic ``AbstractKindModel`` subclasses, so the
``vmauth_auth`` field on ``VictoriaInstance`` is declared with
``KindModelSelectorType`` and the framework deserializes the stored dict
into the right subclass based on the ``kind`` field.

Phase A ships ``BasicAuth`` (HTTP Basic). Future kinds (e.g. ``OidcAuth``
for JWT validation against Core IAM JWKS) can be added here without
changing the model or manifest grammar.
"""

from __future__ import annotations

from restalchemy.dm import models as ra_models
from restalchemy.dm import properties, types_dynamic
from restalchemy.dm import types as ra_types


class AbstractAuthMethod(
    ra_models.SimpleViewMixin,
    types_dynamic.AbstractKindModel,
):
    """Abstract vmauth authentication method.

    Subclasses implement a specific auth kind (basic, oidc, etc.).
    The ``kind`` field is auto-populated by ``AbstractKindModel`` from
    the class' ``KIND`` attribute.
    """


class BasicAuth(AbstractAuthMethod):
    """vmauth authentication via HTTP Basic Auth.

    The username/password pair is rendered into vmauth.yaml's ``users``
    section. Grafana datasources reference the same pair to authenticate
    read requests.
    """

    KIND = "basic"

    username = properties.property(
        ra_types.String(min_length=1, max_length=128), required=True
    )
    password = properties.property(
        ra_types.String(min_length=1, max_length=256), required=True
    )
