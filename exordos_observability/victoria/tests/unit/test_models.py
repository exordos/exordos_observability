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

import pytest

from exordos_observability.common.version_ref import (
    build_version_ref,
    normalize_image,
    parse_image,
    parse_version_ref,
)
from exordos_observability.victoria.controlplane.dm import models


class _DiskShrinkProp:
    def __init__(self, old_value, dirty):
        self.old_value = old_value
        self._dirty = dirty

    def is_dirty(self):
        return self._dirty


class _DiskShrinkStub:
    def __init__(self, metrics_disk_size, logs_disk_size, properties):
        self.metrics_disk_size = metrics_disk_size
        self.logs_disk_size = logs_disk_size
        self.properties = properties

    _validate_update = models.VictoriaInstance._validate_update


class TestVictoriaVersion:
    def test_tablename(self) -> None:
        assert models.VictoriaVersion.__tablename__ == "victoria_versions"

    def test_version_ref_is_stored_field(self) -> None:
        """version_ref must be a stored property, not a computed @property,
        so the core-agent includes it in actual_resource.value."""
        assert "version_ref" in models.VictoriaVersion.properties

    def test_build_version_ref_with_http_image(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img = "https://repo.example.com/img.raw.zst"
        ref = build_version_ref("victoria", str(uid), img)
        assert ref == f"urn:exordos:victoria:{uid}:{img}"

    def test_build_version_ref_with_urn_image_extracts_uuid(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img_uuid = "3cd45e03-7925-5ca1-9eec-9bd481f94a89"
        img = f"urn:images:{img_uuid}"
        ref = build_version_ref("victoria", str(uid), img)
        assert ref == f"urn:exordos:victoria:{uid}:{img_uuid}"

    def test_build_version_ref_round_trips_through_parse(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img = "https://repo.example.com/img.raw.zst"
        ref = build_version_ref("victoria", str(uid), img)
        slug, parsed_uuid, parsed_image = parse_version_ref(ref)
        assert slug == "victoria"
        assert parsed_uuid == str(uid)
        assert parsed_image == img
        assert parse_image(ref) == img

    def test_build_version_ref_changes_when_image_changes(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        ref1 = build_version_ref(
            "victoria", str(uid), "https://repo.example.com/img1.raw.zst"
        )
        ref2 = build_version_ref(
            "victoria", str(uid), "https://repo.example.com/img2.raw.zst"
        )
        assert ref1 != ref2

    def test_build_version_ref_same_for_same_uuid_and_image(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img = "https://repo.example.com/img.raw.zst"
        assert build_version_ref("victoria", str(uid), img) == build_version_ref(
            "victoria", str(uid), img
        )

    def test_normalize_image_extracts_uuid_from_urn(self) -> None:
        assert (
            normalize_image("urn:images:3cd45e03-7925-5ca1-9eec-9bd481f94a89")
            == "3cd45e03-7925-5ca1-9eec-9bd481f94a89"
        )

    def test_normalize_image_passes_http_url_through(self) -> None:
        assert (
            normalize_image("https://repo.example.com/img.raw.zst")
            == "https://repo.example.com/img.raw.zst"
        )


class TestVictoriaInstance:
    def test_tablename(self) -> None:
        assert models.VictoriaInstance.__tablename__ == "victoria_instances"

    def test_status_values(self) -> None:
        values = [s.value for s in models.VictoriaStatus]
        assert "NEW" in values
        assert "IN_PROGRESS" in values
        assert "ACTIVE" in values
        assert "ERROR" in values


class TestValidateUpdate:
    def test_no_shrink_ok(self) -> None:
        stub = _DiskShrinkStub(
            metrics_disk_size=20,
            logs_disk_size=20,
            properties={
                "metrics_disk_size": _DiskShrinkProp(old_value=10, dirty=True),
                "logs_disk_size": _DiskShrinkProp(old_value=20, dirty=False),
            },
        )
        stub._validate_update()  # must not raise

    def test_metrics_disk_shrink_raises(self) -> None:
        stub = _DiskShrinkStub(
            metrics_disk_size=10,
            logs_disk_size=20,
            properties={
                "metrics_disk_size": _DiskShrinkProp(old_value=20, dirty=True),
                "logs_disk_size": _DiskShrinkProp(old_value=20, dirty=False),
            },
        )
        with pytest.raises(ValueError):
            stub._validate_update()

    def test_logs_disk_shrink_raises(self) -> None:
        stub = _DiskShrinkStub(
            metrics_disk_size=20,
            logs_disk_size=10,
            properties={
                "metrics_disk_size": _DiskShrinkProp(old_value=20, dirty=False),
                "logs_disk_size": _DiskShrinkProp(old_value=20, dirty=True),
            },
        )
        with pytest.raises(ValueError):
            stub._validate_update()
