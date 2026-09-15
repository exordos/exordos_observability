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

import exordos_observability.victoria.dataplane.driver as drv_module


class _Stub:
    def __init__(self, name):
        self.name = name

    _build_marker = drv_module.VictoriaInstance._build_marker


class TestBuildMarker:
    def test_contains_name(self) -> None:
        inst = _Stub("my-victoria")
        marker = inst._build_marker()
        assert "VICTORIA_INSTANCE_NAME=my-victoria" in marker

    def test_contains_comment(self) -> None:
        inst = _Stub("x")
        marker = inst._build_marker()
        assert marker.startswith("# Victoria node identity marker")

    def test_ends_with_newline(self) -> None:
        inst = _Stub("x")
        assert inst._build_marker().endswith("\n")


class TestWriteFileAtomic:
    def test_returns_true_when_changed(self, tmp_path) -> None:
        p = tmp_path / "victoria_node.env"
        assert drv_module._write_file_atomic(str(p), "new\n") is True

    def test_returns_false_when_unchanged(self, tmp_path) -> None:
        p = tmp_path / "victoria_node.env"
        p.write_text("same\n")
        assert drv_module._write_file_atomic(str(p), "same\n") is False

    def test_overwrites_different_content(self, tmp_path) -> None:
        p = tmp_path / "victoria_node.env"
        p.write_text("old\n")
        drv_module._write_file_atomic(str(p), "new\n")
        assert p.read_text() == "new\n"


class TestRestoreFromDp:
    def test_parses_name_back(self, tmp_path, monkeypatch) -> None:
        p = tmp_path / "victoria_node.env"
        p.write_text(
            "# Victoria node identity marker\nVICTORIA_INSTANCE_NAME=restored\n"
        )
        monkeypatch.setattr(drv_module, "NODE_MARKER_FILE", str(p))

        inst = drv_module.VictoriaInstance(uuid=sys_uuid.uuid4(), name="placeholder")
        inst.restore_from_dp()
        assert inst.name == "restored"

    def test_missing_file_is_noop(self, tmp_path, monkeypatch) -> None:
        p = tmp_path / "does-not-exist.env"
        monkeypatch.setattr(drv_module, "NODE_MARKER_FILE", str(p))

        inst = drv_module.VictoriaInstance(uuid=sys_uuid.uuid4(), name="unchanged")
        inst.restore_from_dp()
        assert inst.name == "unchanged"
