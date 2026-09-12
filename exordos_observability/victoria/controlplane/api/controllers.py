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

from gcl_iam import controllers as iam_controllers
from restalchemy.api import constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources as ra_resources

from exordos_observability.victoria.controlplane.dm import models


class VictoriaController(ra_controllers.RoutesListController):
    """Controller for /v1/types/victoria/ endpoint"""

    __TARGET_PATH__ = "/v1/types/victoria/"


class VictoriaVersionController(
    iam_controllers.PolicyBasedWithoutProjectController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "exordos_observability"
    __policy_name__ = "victoria_version"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.VictoriaVersion,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "version_ref": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )


class VictoriaInstanceController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "exordos_observability"
    __policy_name__ = "victoria_instance"

    __resource__ = ra_resources.ResourceByRAModel(
        model_class=models.VictoriaInstance,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "ipsv4": {constants.ALL: field_p.Permissions.RO},
                "metrics_endpoint": {constants.ALL: field_p.Permissions.RO},
                "logs_endpoint": {constants.ALL: field_p.Permissions.RO},
                "vmauth": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )
