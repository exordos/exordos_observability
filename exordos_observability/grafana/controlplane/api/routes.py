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

from exordos_observability.grafana.controlplane.api import controllers


class GrafanaDatasourceRoute(routes.Route):
    __controller__ = controllers.GrafanaDatasourceController


class GrafanaDashboardRoute(routes.Route):
    __controller__ = controllers.GrafanaDashboardController


class GrafanaInstanceRoute(routes.Route):
    __controller__ = controllers.GrafanaInstanceController

    # /v1/types/grafana/instances/<uuid>/datasources/[<uuid>]
    datasources = routes.route(GrafanaDatasourceRoute, resource_route=True)
    # /v1/types/grafana/instances/<uuid>/dashboards/[<uuid>]
    dashboards = routes.route(GrafanaDashboardRoute, resource_route=True)


class GrafanaVersionRoute(routes.Route):
    __controller__ = controllers.GrafanaVersionController


class GrafanaArtifactDashboardRoute(routes.Route):
    __controller__ = controllers.GrafanaArtifactDashboardController


class GrafanaRoute(routes.Route):
    """Handler for /v1/types/grafana/ endpoint (mounted by metapaas)."""

    __controller__ = controllers.GrafanaController
    __allow_methods__ = [routes.FILTER]

    # /v1/types/grafana/instances/[<uuid>]
    instances = routes.route(GrafanaInstanceRoute)
    # /v1/types/grafana/versions/[<uuid>]
    versions = routes.route(GrafanaVersionRoute)
    # /v1/types/grafana/dashboards/[<uuid>]
    dashboards = routes.route(GrafanaArtifactDashboardRoute)
