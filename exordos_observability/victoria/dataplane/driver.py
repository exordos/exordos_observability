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
from __future__ import annotations

import logging
import os

from gcl_sdk.agents.universal import constants as c
from gcl_sdk.agents.universal.drivers import meta
from gcl_sdk.infra import constants as pc
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types

LOG = logging.getLogger(__name__)

NODE_MARKER_FILE = "/var/lib/exordos/exordos_metapaas/victoria_node.env"


def _write_file_atomic(path: str, content: str) -> bool:
    """Write file; return True if content changed."""
    try:
        with open(path, "r") as f:
            existing = f.read()
        if existing == content:
            return False
    except FileNotFoundError:
        pass

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.rename(tmp, path)
    return True


class VictoriaInstance(meta.MetaDataPlaneModel):
    """Data plane model for a single Victoria (VM+VL) node.

    The actual VictoriaMetrics/VictoriaLogs runtime configuration (retention,
    listen ports) is delivered separately by the generic infra Config
    resource (see controlplane/infra/dm/models.py) and applied by systemd
    restarting the victoriametrics/victorialogs units — not by this driver.
    This resource only carries node identity, so the universal agent has a
    per-node target to reconcile and report health/status through, mirroring
    the minimal reference plugin (metapaas_demo).
    """

    name = properties.property(
        ra_types.String(min_length=1, max_length=512),
        required=True,
    )
    status = properties.property(
        ra_types.Enum([s.value for s in pc.InstanceStatus]),
        default=pc.InstanceStatus.ACTIVE.value,
    )

    _meta_fields = {"uuid", "name"}

    def get_meta_model_fields(self) -> set[str] | None:
        return self._meta_fields

    def _build_marker(self) -> str:
        return (
            "# Victoria node identity marker\n"
            "# Managed by Exordos Observability control plane — do not edit manually\n"
            f"VICTORIA_INSTANCE_NAME={self.name}\n"
        )

    def dump_to_dp(self) -> None:
        _write_file_atomic(NODE_MARKER_FILE, self._build_marker())

    def restore_from_dp(self) -> None:
        try:
            with open(NODE_MARKER_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("VICTORIA_INSTANCE_NAME="):
                        self.name = line.split("=", 1)[1]
        except FileNotFoundError:
            pass

    def delete_from_dp(self) -> None:
        # Instance exists along with the VM — nothing to delete
        pass

    def update_on_dp(self) -> None:
        self.dump_to_dp()


class VictoriaCapabilityDriver(meta.MetaFileStorageAgentDriver):
    """Victoria capability driver for the universal agent."""

    VICTORIA_META_PATH = os.path.join(c.WORK_DIR, "victoria_meta.json")

    __model_map__ = {
        "victoria_instance_node": VictoriaInstance,
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, meta_file=self.VICTORIA_META_PATH, **kwargs)
