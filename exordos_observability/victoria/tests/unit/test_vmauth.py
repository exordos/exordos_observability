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

import yaml

from exordos_observability.victoria import constants as c
from exordos_observability.victoria.controlplane.dm import auth as auth_kinds
from exordos_observability.victoria.controlplane.infra.dm import models


def _make_instance(**kwargs) -> models.VictoriaInstance:
    defaults = dict(
        uuid=sys_uuid.uuid4(),
        name="victoria",
        project_id=sys_uuid.uuid4(),
        cpu=2,
        ram=2048,
        metrics_disk_size=20,
        logs_disk_size=20,
        retention_period="30d",
        replicas=1,
        version_ref="urn:exordos:victoria:abc:img",
        vmauth=auth_kinds.BasicAuth(username="grafana-reader", password="s3cr3t"),
    )
    defaults.update(kwargs)
    return models.VictoriaInstance(**defaults)


class TestVmauthConfigRendering:
    def test_render_contains_basic_auth_user(self) -> None:
        inst = _make_instance()
        content = inst._render_vmauth_config()
        assert "username: grafana-reader" in content
        assert "password: s3cr3t" in content

    def test_render_contains_read_only_paths(self) -> None:
        inst = _make_instance()
        content = inst._render_vmauth_config()
        doc = yaml.safe_load(content)
        reader = doc["users"][0]
        assert reader["username"] == "grafana-reader"
        read_paths = reader["url_map"][0]["src_paths"]
        assert any("/api/v1/query" in p for p in read_paths)
        assert "/api/v1/series" in read_paths

    def test_render_contains_write_only_unauthorized_user(self) -> None:
        inst = _make_instance()
        content = inst._render_vmauth_config()
        doc = yaml.safe_load(content)
        assert len(doc["users"]) == 1
        writer = doc["unauthorized_user"]
        metrics_paths = writer["url_map"][0]["src_paths"]
        logs_paths = writer["url_map"][1]["src_paths"]
        assert metrics_paths == ["/write", "/api/v1/write"]
        assert logs_paths == ["/insert/.*"]
        assert writer["url_map"][0]["url_prefix"].endswith(
            f":{c.VICTORIAMETRICS_INTERNAL_PORT}"
        )
        assert writer["url_map"][1]["url_prefix"].endswith(
            f":{c.VICTORIALOGS_HTTP_PORT}"
        )

    def test_render_proxies_metrics_to_localhost(self) -> None:
        inst = _make_instance()
        content = inst._render_vmauth_config()
        assert f"http://127.0.0.1:{c.VICTORIAMETRICS_INTERNAL_PORT}" in content

    def test_render_does_not_proxy_to_vmauth_port(self) -> None:
        # vmauth must not proxy to its own listening port — that would
        # cause an infinite loop (vmauth → vmauth → ...).
        inst = _make_instance()
        content = inst._render_vmauth_config()
        assert f"127.0.0.1:{c.VICTORIAMETRICS_HTTP_PORT}" not in content

    def test_render_proxies_logs_to_localhost(self) -> None:
        inst = _make_instance()
        content = inst._render_vmauth_config()
        assert f"http://127.0.0.1:{c.VICTORIALOGS_HTTP_PORT}" in content

    def test_render_changes_when_password_changes(self) -> None:
        inst = _make_instance()
        old = inst._render_vmauth_config()
        inst.vmauth = auth_kinds.BasicAuth(username="grafana-reader", password="new")
        new = inst._render_vmauth_config()
        assert old != new


class TestVictoriaConfLoopback:
    def test_victoria_env_binds_to_loopback(self) -> None:
        inst = _make_instance()
        contents = inst._render_node_configs()
        env_content = contents[c.VICTORIA_ENV_FILE]
        assert "VM_HTTP_LISTEN_ADDR=127.0.0.1:" in env_content
        assert "VL_HTTP_LISTEN_ADDR=127.0.0.1:" in env_content

    def test_victoria_env_uses_internal_port(self) -> None:
        # VM must listen on the internal port, not the vmauth port.
        inst = _make_instance()
        contents = inst._render_node_configs()
        env_content = contents[c.VICTORIA_ENV_FILE]
        assert f"127.0.0.1:{c.VICTORIAMETRICS_INTERNAL_PORT}" in env_content
        assert f"127.0.0.1:{c.VICTORIAMETRICS_HTTP_PORT}" not in env_content


class TestCreateConfigs:
    def test_vmauth_config_uses_vmauth_path(self) -> None:
        inst = _make_instance()
        node_uuid = sys_uuid.uuid4()
        configs = inst.create_configs(node_uuid, inst.project_id)
        vmauth_cfg = next(cfg for cfg in configs if cfg.path == c.VMAUTH_CONFIG_FILE)
        assert vmauth_cfg.on_change is inst.VmauthReloadFunc

    def test_env_config_uses_env_path(self) -> None:
        inst = _make_instance()
        node_uuid = sys_uuid.uuid4()
        configs = inst.create_configs(node_uuid, inst.project_id)
        env_cfg = next(cfg for cfg in configs if cfg.path == c.VICTORIA_ENV_FILE)
        assert env_cfg.on_change is inst.OnReloadFunc

    def test_config_uuids_are_deterministic(self) -> None:
        inst = _make_instance()
        node_uuid = sys_uuid.uuid4()
        c1 = inst.create_configs(node_uuid, inst.project_id)
        c2 = inst.create_configs(node_uuid, inst.project_id)
        assert [cfg.uuid for cfg in c1] == [cfg.uuid for cfg in c2]

    def test_env_and_vmauth_uuids_differ(self) -> None:
        inst = _make_instance()
        node_uuid = sys_uuid.uuid4()
        configs = inst.create_configs(node_uuid, inst.project_id)
        uuids = [cfg.uuid for cfg in configs]
        assert len(uuids) == len(set(uuids))


class TestVictoriaInstanceVmauthAuth:
    def test_vmauth_is_kind_model_field(self) -> None:
        assert "vmauth" in models.VictoriaInstance.properties

    def test_vmauth_basic_kind(self) -> None:
        inst = _make_instance()
        assert inst.vmauth.KIND == "basic"
        assert inst.vmauth.username == "grafana-reader"
        assert inst.vmauth.password == "s3cr3t"
