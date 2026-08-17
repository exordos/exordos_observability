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

from restalchemy.api import routes

from exordos_observability.victoria.controlplane.api import controllers


class VictoriaInstanceRoute(routes.Route):
    __controller__ = controllers.VictoriaInstanceController


class VictoriaVersionRoute(routes.Route):
    __controller__ = controllers.VictoriaVersionController


class VictoriaRoute(routes.Route):
    """Handler for /v1/types/victoria/ endpoint (mounted by metapaas)."""

    __controller__ = controllers.VictoriaController
    __allow_methods__ = [routes.FILTER]

    # /v1/types/victoria/instances/[<uuid>]
    instances = routes.route(VictoriaInstanceRoute)
    # /v1/types/victoria/versions/[<uuid>]
    versions = routes.route(VictoriaVersionRoute)
