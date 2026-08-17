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

import exordos_observability.grafana.dataplane.driver as drv_module

SAMPLE_DATASOURCES = {
    "11111111-1111-1111-1111-111111111111": {
        "name": "victoria-metrics",
        "type": "prometheus",
        "url": "http://10.0.0.5:8428",
        "is_default": True,
    },
    "22222222-2222-2222-2222-222222222222": {
        "name": "victoria-logs",
        "type": "victoriametrics-logs-datasource",
        "url": "http://10.0.0.5:9428",
        "is_default": False,
    },
}


class TestProvisioningYamlRoundtrip:
    def test_render_then_parse_preserves_uuid_keys(self, tmp_path) -> None:
        content = drv_module._render_provisioning_yaml(SAMPLE_DATASOURCES)
        p = tmp_path / "exordos.yaml"
        p.write_text(content)

        parsed = drv_module._parse_provisioning_yaml(str(p))
        # Keys must be the control-plane datasource UUIDs, not Grafana
        # datasource names — otherwise the reconciliation loop sees a
        # perpetual diff (target keyed by UUID, actual keyed by name)
        # and re-runs dump_to_dp every tick.
        assert set(parsed.keys()) == set(SAMPLE_DATASOURCES.keys())
        vm = parsed["11111111-1111-1111-1111-111111111111"]
        assert vm["name"] == "victoria-metrics"
        assert vm["type"] == "prometheus"
        assert vm["url"] == "http://10.0.0.5:8428"
        assert vm["is_default"] is True
        vl = parsed["22222222-2222-2222-2222-222222222222"]
        assert vl["name"] == "victoria-logs"
        assert vl["is_default"] is False

    def test_render_then_parse_roundtrip_is_identity(self, tmp_path) -> None:
        # render -> parse -> render must be stable so the second
        # dump_to_dp produces byte-identical YAML (no reload storm).
        first = drv_module._render_provisioning_yaml(SAMPLE_DATASOURCES)
        p = tmp_path / "exordos.yaml"
        p.write_text(first)
        parsed = drv_module._parse_provisioning_yaml(str(p))
        second = drv_module._render_provisioning_yaml(parsed)
        assert first == second

    def test_render_is_stable_regardless_of_dict_order(self) -> None:
        reversed_ds = dict(reversed(list(SAMPLE_DATASOURCES.items())))
        assert drv_module._render_provisioning_yaml(
            SAMPLE_DATASOURCES
        ) == drv_module._render_provisioning_yaml(reversed_ds)

    def test_parse_missing_file_returns_empty(self, tmp_path) -> None:
        assert drv_module._parse_provisioning_yaml(str(tmp_path / "missing.yaml")) == {}

    def test_render_includes_api_version(self) -> None:
        content = drv_module._render_provisioning_yaml({})
        assert "apiVersion: 1" in content


class TestWriteFileAtomic:
    def test_returns_true_when_changed(self, tmp_path) -> None:
        p = tmp_path / "sub" / "exordos.yaml"
        assert drv_module._write_file_atomic(str(p), "new\n") is True
        assert p.read_text() == "new\n"

    def test_returns_false_when_unchanged(self, tmp_path) -> None:
        p = tmp_path / "exordos.yaml"
        p.write_text("same\n")
        assert drv_module._write_file_atomic(str(p), "same\n") is False


class TestDumpToDp:
    def test_reloads_only_when_content_changes(self, tmp_path, monkeypatch) -> None:
        target = tmp_path / "exordos.yaml"
        monkeypatch.setattr(
            drv_module.constants, "GRAFANA_DATASOURCES_PROVISIONING_FILE", str(target)
        )
        # Point dashboard paths to tmp as well so dump_to_dp doesn't touch /var.
        db_dir = tmp_path / "dashboards"
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", str(db_dir))
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DASHBOARDS_PROVISIONING_FILE",
            str(tmp_path / "dashboards.yaml"),
        )

        calls = []
        monkeypatch.setattr(
            drv_module,
            "_reload_grafana_provisioning",
            lambda: calls.append(True),
        )

        inst = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(), name="node", datasources=SAMPLE_DATASOURCES
        )
        inst.dump_to_dp()
        assert len(calls) == 1  # first write: content changed, reload triggered

        inst.dump_to_dp()
        assert len(calls) == 1  # second write: content unchanged, no reload

    def test_restore_from_dp_matches_target_datasources(
        self, tmp_path, monkeypatch
    ) -> None:
        # The reconciliation loop compares the target resource hash with
        # the actual resource hash (computed over ``datasources``). If
        # ``restore_from_dp`` rebuilds the dict with different keys than
        # the target, the hashes never match and dump_to_dp fires every
        # tick. This test pins the contract: after dump_to_dp, a fresh
        # model with the same target must restore to an identical dict.
        ds_file = tmp_path / "exordos.yaml"
        db_dir = tmp_path / "dashboards"
        monkeypatch.setattr(
            drv_module.constants, "GRAFANA_DATASOURCES_PROVISIONING_FILE", str(ds_file)
        )
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", str(db_dir))
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DASHBOARDS_PROVISIONING_FILE",
            str(tmp_path / "dashboards.yaml"),
        )
        monkeypatch.setattr(drv_module, "_reload_grafana_provisioning", lambda: None)

        target = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(), name="node", datasources=SAMPLE_DATASOURCES
        )
        target.dump_to_dp()

        restored = drv_module.GrafanaInstance(
            uuid=target.uuid, name="node", datasources={}
        )
        restored.restore_from_dp()

        assert restored.datasources == SAMPLE_DATASOURCES

    def test_restore_from_dp_preserves_basic_auth(self, tmp_path, monkeypatch) -> None:
        ds_file = tmp_path / "exordos.yaml"
        db_dir = tmp_path / "dashboards"
        monkeypatch.setattr(
            drv_module.constants, "GRAFANA_DATASOURCES_PROVISIONING_FILE", str(ds_file)
        )
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", str(db_dir))
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DASHBOARDS_PROVISIONING_FILE",
            str(tmp_path / "dashboards.yaml"),
        )
        monkeypatch.setattr(drv_module, "_reload_grafana_provisioning", lambda: None)

        ds_with_auth = {
            "33333333-3333-3333-3333-333333333333": {
                "name": "secure-ds",
                "type": "prometheus",
                "url": "http://10.0.0.9:8428",
                "is_default": True,
                "auth": {
                    "kind": "basic",
                    "username": "reader",
                    "password": "s3cret",
                },
            },
        }
        target = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(), name="node", datasources=ds_with_auth
        )
        target.dump_to_dp()

        restored = drv_module.GrafanaInstance(
            uuid=target.uuid, name="node", datasources={}
        )
        restored.restore_from_dp()

        assert restored.datasources == ds_with_auth


SAMPLE_DASHBOARDS = {
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {
        "name": "overview",
        "folder": "",
        "content": {"title": "Overview", "panels": []},
    },
    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": {
        "name": "metrics",
        "folder": "Custom",
        "content": {"title": "Metrics", "panels": [{"id": 1}]},
    },
}


class TestDashboardProvisioningYaml:
    def test_render_includes_api_version(self) -> None:
        content = drv_module._render_dashboard_provisioning_yaml({})
        assert "apiVersion: 1" in content

    def test_render_creates_provider_per_folder(self) -> None:
        content = drv_module._render_dashboard_provisioning_yaml(SAMPLE_DASHBOARDS)
        doc = __import__("yaml").safe_load(content)
        folders = sorted(p["folder"] for p in doc["providers"])
        assert folders == ["", "Custom"]

    def test_render_provider_paths_use_safe_dirnames(self) -> None:
        content = drv_module._render_dashboard_provisioning_yaml(SAMPLE_DASHBOARDS)
        doc = __import__("yaml").safe_load(content)
        for p in doc["providers"]:
            if p["folder"] == "":
                assert p["options"]["path"].endswith("root")
            elif p["folder"] == "Custom":
                assert p["options"]["path"].endswith("Custom")


class TestDashboardFiles:
    def test_write_creates_per_folder_files(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)

        drv_module._write_dashboard_files(db_dir, SAMPLE_DASHBOARDS)
        root_json = (
            tmp_path
            / "dashboards"
            / "root"
            / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json"
        )
        assert root_json.exists()
        custom_json = (
            tmp_path
            / "dashboards"
            / "Custom"
            / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.json"
        )
        assert custom_json.exists()

    def test_write_removes_stale_files(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)

        # Write initial set.
        drv_module._write_dashboard_files(db_dir, SAMPLE_DASHBOARDS)
        root_dir = tmp_path / "dashboards" / "root"
        assert (root_dir / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json").exists()

        # Write reduced set — stale file should be removed.
        reduced = {
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": SAMPLE_DASHBOARDS[
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            ],
        }
        drv_module._write_dashboard_files(db_dir, reduced)
        assert not (root_dir / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json").exists()

    def test_verify_keeps_dashboards_when_files_exist(
        self, tmp_path, monkeypatch
    ) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)
        dashboards = dict(SAMPLE_DASHBOARDS)
        drv_module._write_dashboard_files(db_dir, dashboards)
        drv_module._verify_dashboard_files(db_dir, dashboards)
        assert set(dashboards.keys()) == set(SAMPLE_DASHBOARDS.keys())

    def test_verify_removes_missing_dashboards(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)
        dashboards = dict(SAMPLE_DASHBOARDS)
        # No files written — both dashboards should be removed.
        drv_module._verify_dashboard_files(db_dir, dashboards)
        assert dashboards == {}

    def test_verify_removes_only_missing_dashboards(
        self, tmp_path, monkeypatch
    ) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)
        # Write only the root dashboard.
        partial = {
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": SAMPLE_DASHBOARDS[
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            ],
        }
        drv_module._write_dashboard_files(db_dir, partial)

        dashboards = dict(SAMPLE_DASHBOARDS)
        drv_module._verify_dashboard_files(db_dir, dashboards)
        # Only the root dashboard should remain.
        assert set(dashboards.keys()) == {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}


class TestDumpToDpDashboards:
    def test_dump_writes_files_and_yaml(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        prov_file = str(tmp_path / "dashboards.yaml")
        ds_file = str(tmp_path / "datasources.yaml")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DASHBOARDS_PROVISIONING_FILE",
            prov_file,
        )
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DATASOURCES_PROVISIONING_FILE",
            ds_file,
        )
        monkeypatch.setattr(drv_module, "_reload_grafana_provisioning", lambda: None)

        inst = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(),
            name="node",
            dashboards=SAMPLE_DASHBOARDS,
        )
        inst.dump_to_dp()

        # Provisioning YAML written.
        import yaml

        with open(prov_file) as f:
            doc = yaml.safe_load(f)
        assert len(doc["providers"]) == 2

        # Dashboard JSON files written.
        root_json = (
            tmp_path
            / "dashboards"
            / "root"
            / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json"
        )
        assert root_json.exists()
        custom_json = (
            tmp_path
            / "dashboards"
            / "Custom"
            / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.json"
        )
        assert custom_json.exists()

    def test_dump_idempotent_no_reload(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        prov_file = str(tmp_path / "dashboards.yaml")
        ds_file = str(tmp_path / "datasources.yaml")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DASHBOARDS_PROVISIONING_FILE",
            prov_file,
        )
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DATASOURCES_PROVISIONING_FILE",
            ds_file,
        )

        calls = []
        monkeypatch.setattr(
            drv_module,
            "_reload_grafana_provisioning",
            lambda: calls.append(True),
        )

        inst = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(),
            name="node",
            dashboards=SAMPLE_DASHBOARDS,
        )
        inst.dump_to_dp()
        assert len(calls) == 1
        inst.dump_to_dp()
        assert len(calls) == 1  # idempotent
