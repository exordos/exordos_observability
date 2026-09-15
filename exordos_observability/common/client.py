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

import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import core as core_drivers


def create_core_client(
    core_username: str,
    core_password: str,
    core_api_base_url: str,
    project_id: sys_uuid.UUID,
):
    """Create a Core REST API client for interacting with Exordos Core.

    Uses ``RestCoreCapabilityDriver`` internally to handle IAM
    authentication and collection mapping, then extracts the underlying
    HTTP client that supports ``do_action`` calls (e.g. for fetching
    node private keys).
    """
    driver = core_drivers.RestCoreCapabilityDriver(
        username=core_username,
        password=core_password,
        user_api_base_url=core_api_base_url,
        project_id=project_id,
        use_project_scope=True,
        node_set="/v1/compute/sets/",
        config="/v1/config/configs/",
    )
    return driver._client._client
