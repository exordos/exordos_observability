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
from exordos_observability.grafana.controlplane.dm import auth, datasource_auth, models

DATASOURCES_WITH_AUTH = {
    "11111111-1111-1111-1111-111111111111": {
        "name": "victoria-metrics",
        "type": "prometheus",
        "url": "http://10.0.0.5:8428",
        "is_default": True,
        "auth": {
            "kind": "basic",
            "username": "grafana-reader",
            "password": "s3cr3t",
        },
    },
    "22222222-2222-2222-2222-222222222222": {
        "name": "victoria-logs",
        "type": "victoriametrics-logs-datasource",
        "url": "http://10.0.0.5:8428",
        "is_default": False,
    },
}


class TestProvisioningYamlBasicAuth:
    def test_render_includes_basic_auth_when_user_set(self) -> None:
        content = drv_module._render_provisioning_yaml(DATASOURCES_WITH_AUTH)
        assert "basicAuth: true" in content
        assert "basicAuthUser: grafana-reader" in content
        assert "basicAuthPassword: s3cr3t" in content

    def test_render_omits_basic_auth_when_no_auth(self) -> None:
        content = drv_module._render_provisioning_yaml(DATASOURCES_WITH_AUTH)
        doc = __import__("yaml").safe_load(content)
        logs_entry = next(e for e in doc["datasources"] if e["name"] == "victoria-logs")
        assert "basicAuth" not in logs_entry
        assert "basicAuthUser" not in logs_entry

    def test_render_then_parse_roundtrip(self, tmp_path) -> None:
        content = drv_module._render_provisioning_yaml(DATASOURCES_WITH_AUTH)
        p = tmp_path / "exordos.yaml"
        p.write_text(content)
        parsed = drv_module._parse_provisioning_yaml(str(p))
        # Keys are the control-plane datasource UUIDs (via ``uid``),
        # not Grafana datasource names.
        assert parsed["11111111-1111-1111-1111-111111111111"]["auth"] == {
            "kind": "basic",
            "username": "grafana-reader",
            "password": "s3cr3t",
        }
        assert "auth" not in parsed["22222222-2222-2222-2222-222222222222"]


class TestGrafanaDatasourceModel:
    def _make_instance(self):
        return models.GrafanaInstance(
            uuid=sys_uuid.uuid4(),
            name="grafana",
            project_id=sys_uuid.uuid4(),
            cpu=1,
            ram=512,
            root_disk_size=8,
            auth=auth.PasswordAuth(password="admin"),
            version_ref="urn:exordos:grafana:x:y",
        )

    def test_auth_field_is_kind_model(self) -> None:
        assert "auth" in models.GrafanaDatasource.properties

    def test_auth_defaults_to_none(self) -> None:
        inst = self._make_instance()
        ds = models.GrafanaDatasource(
            uuid=sys_uuid.uuid4(),
            name="ds",
            project_id=inst.project_id,
            instance=inst,
            type="prometheus",
            url="http://x",
        )
        assert ds.auth is None

    def test_auth_basic_kind_settable(self) -> None:
        inst = self._make_instance()
        ds = models.GrafanaDatasource(
            uuid=sys_uuid.uuid4(),
            name="ds",
            project_id=inst.project_id,
            instance=inst,
            type="prometheus",
            url="http://x",
            auth=datasource_auth.BasicAuth(username="reader", password="pass"),
        )
        assert ds.auth.KIND == "basic"
        assert ds.auth.username == "reader"
        assert ds.auth.password == "pass"

    def test_auth_to_dp_dict_returns_basic_fields(self) -> None:
        auth = datasource_auth.BasicAuth(username="reader", password="pass")
        assert auth.to_dp_dict() == {
            "kind": "basic",
            "username": "reader",
            "password": "pass",
        }

    def test_auth_none_to_dp_dict_empty(self) -> None:
        # AbstractDatasourceAuth.to_dp_dict returns {} by default
        inst = self._make_instance()
        ds = models.GrafanaDatasource(
            uuid=sys_uuid.uuid4(),
            name="ds",
            project_id=inst.project_id,
            instance=inst,
            type="prometheus",
            url="http://x",
        )
        assert ds.auth is None


class TestDumpToDpWithAuth:
    def test_dump_writes_auth_then_no_reload_on_unchanged(
        self, tmp_path, monkeypatch
    ) -> None:
        target = tmp_path / "exordos.yaml"
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DATASOURCES_PROVISIONING_FILE",
            str(target),
        )
        monkeypatch.setattr(
            drv_module.constants, "GRAFANA_DASHBOARDS_DIR", str(tmp_path / "db")
        )
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
            uuid=sys_uuid.uuid4(),
            name="node",
            datasources=DATASOURCES_WITH_AUTH,
        )
        inst.dump_to_dp()
        assert len(calls) == 1
        inst.dump_to_dp()
        assert len(calls) == 1  # idempotent

    def test_dump_triggers_reload_when_password_changes(
        self, tmp_path, monkeypatch
    ) -> None:
        target = tmp_path / "exordos.yaml"
        monkeypatch.setattr(
            drv_module.constants,
            "GRAFANA_DATASOURCES_PROVISIONING_FILE",
            str(target),
        )
        monkeypatch.setattr(
            drv_module.constants, "GRAFANA_DASHBOARDS_DIR", str(tmp_path / "db")
        )
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

        ds = dict(DATASOURCES_WITH_AUTH)
        inst = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(), name="node", datasources=ds
        )
        inst.dump_to_dp()
        assert len(calls) == 1

        ds2 = {k: dict(v) for k, v in DATASOURCES_WITH_AUTH.items()}
        ds2["11111111-1111-1111-1111-111111111111"]["auth"] = {
            "kind": "basic",
            "username": "grafana-reader",
            "password": "new",
        }
        inst2 = drv_module.GrafanaInstance(uuid=inst.uuid, name="node", datasources=ds2)
        inst2.dump_to_dp()
        assert len(calls) == 2
