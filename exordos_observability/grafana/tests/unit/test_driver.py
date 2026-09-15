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


@pytest.fixture
def _dp_paths(tmp_path, monkeypatch):
    """Point all DP file paths to tmp_path and ensure no stale markers."""
    ds_file = tmp_path / "exordos.yaml"
    db_dir = tmp_path / "dashboards"
    db_yaml = tmp_path / "dashboards.yaml"
    reload_marker = tmp_path / "reload_pending"
    admin_pw = tmp_path / "admin_password"
    monkeypatch.setattr(
        drv_module.constants, "GRAFANA_DATASOURCES_PROVISIONING_FILE", str(ds_file)
    )
    monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", str(db_dir))
    monkeypatch.setattr(
        drv_module.constants,
        "GRAFANA_DASHBOARDS_PROVISIONING_FILE",
        str(db_yaml),
    )
    monkeypatch.setattr(
        drv_module.constants, "GRAFANA_RELOAD_PENDING_FILE", str(reload_marker)
    )
    monkeypatch.setattr(
        drv_module.constants, "GRAFANA_ADMIN_PASSWORD_FILE", str(admin_pw)
    )
    return {
        "ds_file": ds_file,
        "db_dir": db_dir,
        "db_yaml": db_yaml,
        "reload_marker": reload_marker,
        "admin_pw": admin_pw,
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
    def test_reloads_only_when_content_changes(self, _dp_paths, monkeypatch) -> None:
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
        self, _dp_paths, monkeypatch
    ) -> None:
        # The reconciliation loop compares the target resource hash with
        # the actual resource hash (computed over ``datasources``). If
        # ``restore_from_dp`` rebuilds the dict with different keys than
        # the target, the hashes never match and dump_to_dp fires every
        # tick. This test pins the contract: after dump_to_dp, a fresh
        # model with the same target must restore to an identical dict.
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

    def test_restore_from_dp_preserves_basic_auth(self, _dp_paths, monkeypatch) -> None:
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

    def test_dump_to_dp_raises_when_reload_fails(self, _dp_paths, monkeypatch) -> None:
        """Reload failure must propagate, not be swallowed."""
        monkeypatch.setattr(
            drv_module,
            "_reload_grafana_provisioning",
            lambda: (_ for _ in ()).throw(
                drv_module.ProvisioningReloadError(detail="boom")
            ),
        )

        inst = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(), name="node", datasources=SAMPLE_DATASOURCES
        )
        with pytest.raises(drv_module.ProvisioningReloadError):
            inst.dump_to_dp()
        # Marker must be written so the next tick retries.
        assert _dp_paths["reload_marker"].exists()

    def test_reload_retried_when_marker_exists(self, _dp_paths, monkeypatch) -> None:
        """Even with byte-identical files, a pending marker forces a reload."""
        # First tick: write files, reload succeeds.
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
        assert len(calls) == 1
        assert not _dp_paths["reload_marker"].exists()

        # Simulate a failed reload on the second tick (files unchanged).
        def _fail():
            calls.append(True)
            raise drv_module.ProvisioningReloadError(detail="Grafana down")

        monkeypatch.setattr(drv_module, "_reload_grafana_provisioning", _fail)
        inst.dump_to_dp()  # files unchanged, but no marker yet → no reload
        assert len(calls) == 1  # not retried (no marker, no file change)

        # Now write the marker manually (simulating a prior failure).
        _dp_paths["reload_marker"].write_text("pending\n")
        with pytest.raises(drv_module.ProvisioningReloadError):
            inst.dump_to_dp()  # marker exists → reload attempted (fails)
        assert len(calls) == 2
        assert _dp_paths["reload_marker"].exists()  # still pending (failed)

    def test_marker_cleared_on_successful_reload(self, _dp_paths, monkeypatch) -> None:
        """A successful reload after a pending marker clears it."""
        _dp_paths["reload_marker"].write_text("pending\n")
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
        assert len(calls) == 1
        assert not _dp_paths["reload_marker"].exists()

    def test_restore_from_dp_forces_diff_when_marker_exists(
        self, _dp_paths, monkeypatch
    ) -> None:
        """restore_from_dp returns empty datasources when reload is pending,
        so the reconciliation loop sees a diff and retries dump_to_dp."""
        monkeypatch.setattr(drv_module, "_reload_grafana_provisioning", lambda: None)

        # Write files so restore would normally return real datasources.
        target = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(), name="node", datasources=SAMPLE_DATASOURCES
        )
        target.dump_to_dp()

        # Write the marker — simulates a failed reload.
        _dp_paths["reload_marker"].write_text("pending\n")

        restored = drv_module.GrafanaInstance(
            uuid=target.uuid, name="node", datasources={}
        )
        restored.restore_from_dp()
        # Must be empty to force a diff against the target.
        assert restored.datasources == {}

        # After clearing the marker, restore returns real state.
        _dp_paths["reload_marker"].unlink()
        restored2 = drv_module.GrafanaInstance(
            uuid=target.uuid, name="node", datasources={}
        )
        restored2.restore_from_dp()
        assert restored2.datasources == SAMPLE_DATASOURCES


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
            # The on-disk path is derived from the safe (hashed) name,
            # not the human-readable folder title.
            expected = drv_module._folder_to_safe_name(p["folder"])
            assert p["options"]["path"].endswith(expected)
            # The provider name mirrors the safe name too.
            assert p["name"] == f"exordos-{expected}"
        # The ``folder`` field keeps the human-readable title verbatim
        # — that is what Grafana shows in the UI.
        assert "" in [p["folder"] for p in doc["providers"]]
        assert "Custom" in [p["folder"] for p in doc["providers"]]


class TestFolderSafeName:
    """Security and collision properties of ``_folder_to_safe_name``.

    Regression tests for the review finding that the old
    ``_folder_to_dirname`` (which only replaced ``/`` and spaces) allowed
    directory traversal (``..``) and collapsed distinct folder titles
    (``"team ops"`` vs ``"team_ops"``) onto the same on-disk path.
    """

    def test_empty_folder_maps_to_root(self) -> None:
        assert drv_module._folder_to_safe_name("") == drv_module.ROOT_FOLDER_DIR

    def test_no_path_separators_in_safe_name(self) -> None:
        for folder in ["a/b", "a/b/c", "with/slash", "..", "../etc"]:
            name = drv_module._folder_to_safe_name(folder)
            assert "/" not in name
            assert "\\" not in name

    def test_no_dot_segments_in_safe_name(self) -> None:
        """The safe name must never be ``.`` or ``..`` or contain them,
        so it cannot escape ``GRAFANA_DASHBOARDS_DIR`` via traversal."""
        for folder in [".", "..", "...", "....", "../..", "..\\.."]:
            name = drv_module._folder_to_safe_name(folder)
            assert name not in (".", "..")
            assert ".." not in name

    def test_distinct_folders_get_distinct_safe_names(self) -> None:
        """Folders that differ only by characters the old transform
        collapsed (space vs underscore, slash vs underscore) must now
        map to distinct safe names."""
        collisions = [
            ("team ops", "team_ops"),
            ("a/b", "a_b"),
            ("a b", "a_b"),
            ("a  b", "a b"),
        ]
        for a, b in collisions:
            assert drv_module._folder_to_safe_name(a) != (
                drv_module._folder_to_safe_name(b)
            ), f"collision between {a!r} and {b!r}"

    def test_same_folder_gets_same_safe_name(self) -> None:
        """Deterministic: the same folder title always yields the same
        safe name (required for dump/restore idempotency)."""
        for folder in ["Team Ops", "Custom", "", "a/b/c"]:
            assert drv_module._folder_to_safe_name(folder) == (
                drv_module._folder_to_safe_name(folder)
            )

    def test_safe_name_always_within_base_dir(self, tmp_path) -> None:
        """A traversal attempt (``..``) must not produce a path outside
        the dashboards base directory."""
        base = tmp_path / "dashboards"
        malicious = {
            "cccccccc-cccc-cccc-cccc-cccccccccccc": {
                "name": "evil",
                "folder": "..",
                "content": {"title": "Evil", "panels": []},
            },
        }
        drv_module._write_dashboard_files(str(base), malicious)
        # The only created subdirectory must be inside ``base``.
        for entry in base.iterdir():
            assert entry.resolve().is_relative_to(base.resolve()), (
                f"{entry} escaped the base directory"
            )


class TestFolderCollisionProvisioning:
    """Two dashboards with collision-prone folder titles must get
    distinct provider paths/names while keeping distinct human-readable
    ``folder`` titles in the provisioning YAML."""

    COLLISION_DASHBOARDS = {
        "11111111-1111-1111-1111-111111111111": {
            "name": "ops-a",
            "folder": "team ops",
            "content": {"title": "A", "panels": []},
        },
        "22222222-2222-2222-2222-222222222222": {
            "name": "ops-b",
            "folder": "team_ops",
            "content": {"title": "B", "panels": []},
        },
    }

    def test_distinct_paths_and_names_for_colliding_folders(self) -> None:
        import yaml

        content = drv_module._render_dashboard_provisioning_yaml(
            self.COLLISION_DASHBOARDS
        )
        doc = yaml.safe_load(content)
        assert len(doc["providers"]) == 2
        paths = [p["options"]["path"] for p in doc["providers"]]
        names = [p["name"] for p in doc["providers"]]
        # Distinct on-disk paths and provider names.
        assert len(set(paths)) == 2
        assert len(set(names)) == 2
        # Human-readable folder titles preserved verbatim (what Grafana
        # shows in the UI).
        folders = sorted(p["folder"] for p in doc["providers"])
        assert folders == ["team ops", "team_ops"]

    def test_collision_files_written_to_distinct_dirs(self, tmp_path) -> None:
        db_dir = str(tmp_path / "dashboards")
        drv_module._write_dashboard_files(db_dir, self.COLLISION_DASHBOARDS)
        a = (
            tmp_path
            / "dashboards"
            / drv_module._folder_to_safe_name("team ops")
            / "11111111-1111-1111-1111-111111111111.json"
        )
        b = (
            tmp_path
            / "dashboards"
            / drv_module._folder_to_safe_name("team_ops")
            / "22222222-2222-2222-2222-222222222222.json"
        )
        assert a.exists()
        assert b.exists()
        assert a.parent != b.parent


class TestDashboardFiles:
    def test_write_creates_per_folder_files(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)

        drv_module._write_dashboard_files(db_dir, SAMPLE_DASHBOARDS)
        root_json = (
            tmp_path
            / "dashboards"
            / drv_module._folder_to_safe_name("")
            / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json"
        )
        assert root_json.exists()
        custom_json = (
            tmp_path
            / "dashboards"
            / drv_module._folder_to_safe_name("Custom")
            / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.json"
        )
        assert custom_json.exists()

    def test_write_removes_stale_files(self, tmp_path, monkeypatch) -> None:
        db_dir = str(tmp_path / "dashboards")
        monkeypatch.setattr(drv_module.constants, "GRAFANA_DASHBOARDS_DIR", db_dir)

        # Write initial set.
        drv_module._write_dashboard_files(db_dir, SAMPLE_DASHBOARDS)
        root_dir = tmp_path / "dashboards" / drv_module._folder_to_safe_name("")
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
    def test_dump_writes_files_and_yaml(self, _dp_paths, monkeypatch) -> None:
        monkeypatch.setattr(drv_module, "_reload_grafana_provisioning", lambda: None)

        inst = drv_module.GrafanaInstance(
            uuid=sys_uuid.uuid4(),
            name="node",
            dashboards=SAMPLE_DASHBOARDS,
        )
        inst.dump_to_dp()

        # Provisioning YAML written.
        import yaml

        with open(_dp_paths["db_yaml"]) as f:
            doc = yaml.safe_load(f)
        assert len(doc["providers"]) == 2

        # Dashboard JSON files written.
        root_json = (
            _dp_paths["db_dir"]
            / drv_module._folder_to_safe_name("")
            / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json"
        )
        assert root_json.exists()
        custom_json = (
            _dp_paths["db_dir"]
            / drv_module._folder_to_safe_name("Custom")
            / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.json"
        )
        assert custom_json.exists()

    def test_dump_idempotent_no_reload(self, _dp_paths, monkeypatch) -> None:
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


class TestReadGrafanaAdminCredentials:
    """Tests for _read_grafana_admin_credentials credential resolution."""

    def test_prefers_durable_admin_password_file(self, _dp_paths, monkeypatch) -> None:
        """When the durable admin password file exists, it takes
        precedence over GF_SECURITY_ADMIN_PASSWORD in the env file.

        This is the OIDC deployment case: the env file does NOT carry
        GF_SECURITY_ADMIN_PASSWORD, and the password is generated once
        by the DP bootstrap script.
        """
        _dp_paths["admin_pw"].write_text("durable-secret")
        # Also write an env file with a different password — must be
        # ignored because the durable file exists.
        env_file = _dp_paths["ds_file"].parent / "grafana.env"
        env_file.write_text(
            "GF_SECURITY_ADMIN_USER=admin\nGF_SECURITY_ADMIN_PASSWORD=env-secret\n"
        )
        monkeypatch.setattr(drv_module.constants, "GRAFANA_ENV_FILE", str(env_file))
        user, password = drv_module._read_grafana_admin_credentials()
        assert user == "admin"
        assert password == "durable-secret"

    def test_falls_back_to_env_file_without_durable(
        self, _dp_paths, monkeypatch
    ) -> None:
        """Without the durable file, the env file password is used.

        This is the password-auth deployment case: the control plane
        delivers GF_SECURITY_ADMIN_PASSWORD via the env file.
        """
        # No admin_pw file.
        env_file = _dp_paths["ds_file"].parent / "grafana.env"
        env_file.write_text(
            "GF_SECURITY_ADMIN_USER=admin\nGF_SECURITY_ADMIN_PASSWORD=env-secret\n"
        )
        monkeypatch.setattr(drv_module.constants, "GRAFANA_ENV_FILE", str(env_file))
        user, password = drv_module._read_grafana_admin_credentials()
        assert user == "admin"
        assert password == "env-secret"

    def test_defaults_when_no_files_exist(self, _dp_paths, monkeypatch) -> None:
        """Both files missing → default admin/admin (first-boot fallback)."""
        env_file = _dp_paths["ds_file"].parent / "grafana.env"
        monkeypatch.setattr(drv_module.constants, "GRAFANA_ENV_FILE", str(env_file))
        user, password = drv_module._read_grafana_admin_credentials()
        assert user == "admin"
        assert password == "admin"
