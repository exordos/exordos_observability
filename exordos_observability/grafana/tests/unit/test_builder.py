#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""Unit tests for GrafanaInstanceBuilder dashboard content resolution."""

import uuid as sys_uuid

import pytest
from restalchemy.storage.sql import orm

from exordos_observability.grafana import constants as c
from exordos_observability.grafana.controlplane.dm import auth as auth_kinds
from exordos_observability.grafana.controlplane.dm import models as cp_models
from exordos_observability.grafana.controlplane.paas.dm import models as paas_models
from exordos_observability.grafana.controlplane.paas.services import builder


@pytest.fixture
def b():
    return builder.GrafanaInstanceBuilder()


PID = sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142")


def _make_instance():
    return cp_models.GrafanaInstance(
        name="i",
        project_id=PID,
        cpu=1,
        ram=512,
        root_disk_size=8,
        version_ref="x",
        auth=auth_kinds.PasswordAuth(password="x"),
    )


def _make_dashboard(**kwargs):
    kwargs.setdefault("name", "foo")
    kwargs.setdefault("project_id", PID)
    kwargs.setdefault("instance", _make_instance())
    return cp_models.GrafanaDashboard(**kwargs)


def _mock_get_one_or_none(monkeypatch, return_value):
    """Patch ObjectCollection.get_one_or_none at the class level."""
    if return_value is _FAIL:

        def fake_get_one_or_none(self, *args, **kwargs):
            pytest.fail("should not query")
    else:

        def fake_get_one_or_none(self, *args, **kwargs):
            return return_value

    monkeypatch.setattr(orm.ObjectCollection, "get_one_or_none", fake_get_one_or_none)


_FAIL = object()


class TestRefreshDashboardContent:
    def test_skips_when_up_to_date(self, b, monkeypatch) -> None:
        ref = "abc-123_https://x.com/d.json"
        dash = _make_dashboard(
            version_ref=ref,
            saved_version_ref=ref,
            content={"title": "foo"},
        )
        _mock_get_one_or_none(monkeypatch, _FAIL)
        b._refresh_dashboard_content(dash)
        assert dash.content == {"title": "foo"}

    def test_fetches_content_when_version_ref_changed(self, b, monkeypatch) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        url = "https://x.com/d.json"
        ref = f"{uid}_{url}"

        from exordos_observability.grafana.controlplane.dm import sources

        artifact = cp_models.GrafanaArtifactDashboard(
            uuid=uid,
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=sources.RawDashboardSource(content={"title": "fetched"}),
        )

        _mock_get_one_or_none(monkeypatch, artifact)

        updated = {}

        def fake_update(self, session=None, force=False):
            updated.setdefault("statuses", []).append(self.status)
            updated["content"] = self.content
            updated["saved_version_ref"] = self.saved_version_ref

        monkeypatch.setattr(cp_models.GrafanaDashboard, "update", fake_update)

        dash = _make_dashboard(
            version_ref=ref,
            saved_version_ref=None,
            content=None,
        )
        b._refresh_dashboard_content(dash)

        assert updated["content"] == {"title": "fetched"}
        assert updated["saved_version_ref"] == ref
        # Should transition through IN_PROGRESS then to ACTIVE.
        assert cp_models.GrafanaStatus.IN_PROGRESS.value in updated["statuses"]
        assert updated["statuses"][-1] == cp_models.GrafanaStatus.ACTIVE.value

    def test_skips_when_artifact_not_found(self, b, monkeypatch) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        ref = f"{uid}_https://x.com/d.json"

        _mock_get_one_or_none(monkeypatch, None)

        updated = {}

        def fake_update(self, session=None, force=False):
            updated["status"] = self.status

        monkeypatch.setattr(cp_models.GrafanaDashboard, "update", fake_update)

        dash = _make_dashboard(
            version_ref=ref,
            saved_version_ref=None,
            content=None,
        )
        b._refresh_dashboard_content(dash)
        assert dash.content is None
        assert dash.saved_version_ref is None
        assert updated.get("status") == cp_models.GrafanaStatus.ERROR.value

    def test_skips_when_version_ref_unparseable(self, b, monkeypatch) -> None:
        _mock_get_one_or_none(monkeypatch, _FAIL)

        updated = {}

        def fake_update(self, session=None, force=False):
            updated["status"] = self.status

        monkeypatch.setattr(cp_models.GrafanaDashboard, "update", fake_update)

        dash = _make_dashboard(
            version_ref="not-a-uuid-based-ref",
            saved_version_ref=None,
            content=None,
        )
        b._refresh_dashboard_content(dash)
        assert dash.content is None
        assert updated.get("status") == cp_models.GrafanaStatus.ERROR.value

    def test_skips_when_source_fetch_raises(self, b, monkeypatch) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        urn = "urn:artifacts:87654321-4321-4321-4321-210987654321"
        ref = f"{uid}_{urn}"

        from exordos_observability.grafana.controlplane.dm import sources

        source = sources.UrnDashboardSource(urn=urn)
        monkeypatch.setattr(
            source,
            "_fetch_dashboard",
            lambda core_client=None: (_ for _ in ()).throw(
                RuntimeError("network down")
            ),
        )

        artifact = cp_models.GrafanaArtifactDashboard(
            uuid=uid,
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=source,
        )

        _mock_get_one_or_none(monkeypatch, artifact)

        updated = {}

        def fake_update(self, session=None, force=False):
            updated.setdefault("statuses", []).append(self.status)

        monkeypatch.setattr(cp_models.GrafanaDashboard, "update", fake_update)

        dash = _make_dashboard(
            version_ref=ref,
            saved_version_ref=None,
            content=None,
        )
        b._refresh_dashboard_content(dash)
        assert dash.content is None
        assert dash.saved_version_ref is None
        # Should transition through IN_PROGRESS then to ERROR.
        assert cp_models.GrafanaStatus.IN_PROGRESS.value in updated["statuses"]
        assert updated["statuses"][-1] == cp_models.GrafanaStatus.ERROR.value

    def test_non_object_json_marks_error_and_continues(self, b, monkeypatch) -> None:
        """A source returning valid JSON that is not an object (e.g. an
        array) must not crash the iteration — the dashboard is marked
        ERROR and the loop continues with the remaining dashboards."""
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        urn = "urn:artifacts:87654321-4321-4321-4321-210987654321"
        ref = f"{uid}_{urn}"

        from exordos_observability.grafana.controlplane.dm import sources

        source = sources.UrnDashboardSource(urn=urn)
        # Source returns a JSON array, not an object.
        monkeypatch.setattr(
            source, "_fetch_dashboard", lambda core_client=None: []
        )

        artifact = cp_models.GrafanaArtifactDashboard(
            uuid=uid,
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=source,
        )

        _mock_get_one_or_none(monkeypatch, artifact)

        updated = {}

        def fake_update(self, session=None, force=False):
            updated.setdefault("statuses", []).append(self.status)

        monkeypatch.setattr(cp_models.GrafanaDashboard, "update", fake_update)

        dash = _make_dashboard(
            version_ref=ref,
            saved_version_ref=None,
            content=None,
        )
        b._refresh_dashboard_content(dash)
        # Content must not be set to a non-dict value.
        assert dash.content is None
        assert dash.saved_version_ref is None
        # Should transition through IN_PROGRESS then to ERROR.
        assert cp_models.GrafanaStatus.IN_PROGRESS.value in updated["statuses"]
        assert updated["statuses"][-1] == cp_models.GrafanaStatus.ERROR.value

    def test_non_object_json_does_not_abort_other_dashboards(
        self, b, monkeypatch
    ) -> None:
        """When one dashboard returns a JSON array, the remaining
        dashboards in the same ``_get_dashboards`` iteration must still
        be processed."""
        uid_bad = sys_uuid.UUID("11111111-1234-1234-1234-123456789012")
        uid_ok = sys_uuid.UUID("22222222-1234-1234-1234-123456789012")
        ref_bad = f"{uid_bad}_raw"
        ref_ok = f"{uid_ok}_raw"

        from exordos_observability.grafana.controlplane.dm import sources

        # Bad artifact returns a JSON array.
        bad_source = sources.RawDashboardSource(content={"title": "x"})
        monkeypatch.setattr(
            bad_source, "_fetch_dashboard", lambda core_client=None: []
        )
        bad_artifact = cp_models.GrafanaArtifactDashboard(
            uuid=uid_bad,
            name="bad",
            project_id=PID,
            source=bad_source,
        )

        # Good artifact returns a valid dashboard object.
        ok_source = sources.RawDashboardSource(content={"title": "y"})
        ok_artifact = cp_models.GrafanaArtifactDashboard(
            uuid=uid_ok,
            name="ok",
            project_id=PID,
            source=ok_source,
        )

        artifacts = {uid_bad: bad_artifact, uid_ok: ok_artifact}

        def fake_get_one_or_none(self, *args, **kwargs):
            filters = kwargs.get("filters", {})
            uuid_filter = filters.get("uuid")
            if uuid_filter is not None:
                return artifacts.get(uuid_filter.value)
            return None

        monkeypatch.setattr(
            orm.ObjectCollection, "get_one_or_none", fake_get_one_or_none
        )
        monkeypatch.setattr(
            cp_models.GrafanaDashboard, "update", lambda self, **kw: None
        )

        dash_bad = _make_dashboard(
            version_ref=ref_bad, saved_version_ref=None, content=None
        )
        dash_ok = _make_dashboard(
            version_ref=ref_ok, saved_version_ref=None, content=None
        )

        inst = paas_models.GrafanaInstance(
            name="i",
            project_id=PID,
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth_kinds.PasswordAuth(password="x"),
        )
        monkeypatch.setattr(inst, "get_dashboards", lambda: [dash_bad, dash_ok])

        result = b._get_dashboards(inst)
        # The bad dashboard must be absent (ERROR, content not set).
        assert str(dash_bad.uuid) not in result
        # The good dashboard must be present — iteration was not aborted.
        assert str(dash_ok.uuid) in result
        assert result[str(dash_ok.uuid)]["content"] == {"title": "y"}

    def test_passes_core_client_to_source(self, monkeypatch) -> None:
        """UrnDashboardSource needs the builder's core client to resolve the
        artifact URN, so it must be threaded through dashboard()."""
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        urn = "urn:artifacts:87654321-4321-4321-4321-210987654321"
        ref = f"{uid}_{urn}"

        from exordos_observability.grafana.controlplane.dm import sources

        sentinel_client = object()
        b = builder.GrafanaInstanceBuilder()
        b._cclient = sentinel_client

        received = {}

        def fake_fetch(core_client=None):
            received["core_client"] = core_client
            return {"title": "ok"}

        source = sources.UrnDashboardSource(urn=urn)
        monkeypatch.setattr(source, "_fetch_dashboard", fake_fetch)

        artifact = cp_models.GrafanaArtifactDashboard(
            uuid=uid,
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=source,
        )

        _mock_get_one_or_none(monkeypatch, artifact)
        monkeypatch.setattr(
            cp_models.GrafanaDashboard, "update", lambda self, **kwargs: None
        )

        dash = _make_dashboard(version_ref=ref, saved_version_ref=None, content=None)
        b._refresh_dashboard_content(dash)

        assert received["core_client"] is sentinel_client


class TestActualizePaasObjects:
    """Tests for child status actualization in actualize_paas_objects."""

    def _make_paas_instance(self):
        return paas_models.GrafanaInstance(
            name="i",
            project_id=PID,
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth_kinds.PasswordAuth(password="x"),
        )

    def test_marks_children_active_when_derivative_active(self, b, monkeypatch) -> None:
        from gcl_sdk.infra import constants as pc
        from gcl_sdk.paas.services import builder as sdk_builder

        inst = self._make_paas_instance()

        ds = cp_models.GrafanaDatasource(
            name="ds",
            project_id=PID,
            instance=inst,
            type="prometheus",
            url="http://x:8428",
        )
        ds.status = cp_models.GrafanaStatus.NEW.value

        ref = "11111111-1234-1234-1234-123456789012_raw"
        dash = cp_models.GrafanaDashboard(
            name="d",
            project_id=PID,
            instance=inst,
            version_ref=ref,
            saved_version_ref=ref,
            content={"title": "ok"},
        )
        dash.status = cp_models.GrafanaStatus.IN_PROGRESS.value

        monkeypatch.setattr(inst, "get_datasources", lambda: [ds])
        monkeypatch.setattr(inst, "get_dashboards", lambda: [dash])
        monkeypatch.setattr(
            inst,
            "get_actual_nodeset",
            lambda: type(
                "NS", (), {"nodes": {"d0265438-3875-431d-9a39-7f721127dc80": {}}}
            )(),
        )

        updated = []

        def fake_ds_update(self, session=None, force=False):
            updated.append(("ds", self.status))

        def fake_dash_update(self, session=None, force=False):
            updated.append(("dash", self.status))

        monkeypatch.setattr(cp_models.GrafanaDatasource, "update", fake_ds_update)
        monkeypatch.setattr(cp_models.GrafanaDashboard, "update", fake_dash_update)

        # Derivative is ACTIVE on the dataplane.
        active_node = type("Node", (), {"status": pc.InstanceStatus.ACTIVE.value})()
        pair = sdk_builder.PaaSResourcePair(
            target=type("T", (), {"status": "NEW"})(),
            actual=active_node,
        )
        collection = sdk_builder.PaaSCollection(paas_objects=(pair,))

        b.actualize_paas_objects(inst, collection)

        assert ("ds", cp_models.GrafanaStatus.ACTIVE.value) in updated
        assert ("dash", cp_models.GrafanaStatus.ACTIVE.value) in updated

    def test_does_not_mark_children_when_derivative_not_active(
        self, b, monkeypatch
    ) -> None:
        from gcl_sdk.infra import constants as pc
        from gcl_sdk.paas.services import builder as sdk_builder

        inst = self._make_paas_instance()

        ds = cp_models.GrafanaDatasource(
            name="ds",
            project_id=PID,
            instance=inst,
            type="prometheus",
            url="http://x:8428",
        )
        ds.status = cp_models.GrafanaStatus.NEW.value

        monkeypatch.setattr(inst, "get_datasources", lambda: [ds])
        monkeypatch.setattr(inst, "get_dashboards", list)
        monkeypatch.setattr(
            inst,
            "get_actual_nodeset",
            lambda: type(
                "NS", (), {"nodes": {"d0265438-3875-431d-9a39-7f721127dc80": {}}}
            )(),
        )

        monkeypatch.setattr(
            cp_models.GrafanaDatasource,
            "update",
            lambda self, **kw: pytest.fail("should not update"),
        )

        # Derivative is IN_PROGRESS (not yet delivered).
        pair = sdk_builder.PaaSResourcePair(
            target=type("T", (), {"status": "NEW"})(),
            actual=type("Node", (), {"status": pc.InstanceStatus.IN_PROGRESS.value})(),
        )
        collection = sdk_builder.PaaSCollection(paas_objects=(pair,))

        b.actualize_paas_objects(inst, collection)
        assert ds.status == cp_models.GrafanaStatus.NEW.value

    def test_does_not_mark_unresolved_dashboard_active(self, b, monkeypatch) -> None:
        from gcl_sdk.infra import constants as pc
        from gcl_sdk.paas.services import builder as sdk_builder

        inst = self._make_paas_instance()

        ref = "11111111-1234-1234-1234-123456789012_raw"
        dash = cp_models.GrafanaDashboard(
            name="d",
            project_id=PID,
            instance=inst,
            version_ref=ref,
            saved_version_ref=None,
            content=None,
        )
        dash.status = cp_models.GrafanaStatus.IN_PROGRESS.value

        monkeypatch.setattr(inst, "get_datasources", list)
        monkeypatch.setattr(inst, "get_dashboards", lambda: [dash])

        # Stub _build_paas_objects to avoid DB/HTTP access — we only
        # care about the status logic in actualize_paas_objects.
        monkeypatch.setattr(b, "_build_paas_objects", lambda inst: [])

        monkeypatch.setattr(
            cp_models.GrafanaDashboard,
            "update",
            lambda self, **kw: pytest.fail("should not update"),
        )

        active_node = type("Node", (), {"status": pc.InstanceStatus.ACTIVE.value})()
        pair = sdk_builder.PaaSResourcePair(
            target=type("T", (), {"status": "NEW"})(),
            actual=active_node,
        )
        collection = sdk_builder.PaaSCollection(paas_objects=(pair,))

        b.actualize_paas_objects(inst, collection)
        # Unresolved dashboard stays IN_PROGRESS.
        assert dash.status == cp_models.GrafanaStatus.IN_PROGRESS.value


class TestGetDatasources:
    """Tests for _get_datasources empty-url filtering."""

    def _make_instance(self):
        return paas_models.GrafanaInstance(
            name="i",
            project_id=PID,
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth_kinds.PasswordAuth(password="x"),
        )

    def test_skips_datasources_with_empty_url(self, b, monkeypatch) -> None:
        inst = self._make_instance()

        ds_empty = cp_models.GrafanaDatasource(
            name="pending",
            project_id=PID,
            instance=inst,
            type="prometheus",
            url="",
        )
        ds_ready = cp_models.GrafanaDatasource(
            name="ready",
            project_id=PID,
            instance=inst,
            type="prometheus",
            url="http://10.0.0.1:8428",
        )
        monkeypatch.setattr(inst, "get_datasources", lambda: [ds_empty, ds_ready])

        result = b._get_datasources(inst)

        assert len(result) == 1
        (_, ds_data) = result.popitem()
        assert ds_data["name"] == "ready"
        assert ds_data["url"] == "http://10.0.0.1:8428"

    def test_includes_all_datasources_when_urls_resolved(self, b, monkeypatch) -> None:
        inst = self._make_instance()

        ds1 = cp_models.GrafanaDatasource(
            name="metrics",
            project_id=PID,
            instance=inst,
            type="prometheus",
            url="http://10.0.0.1:8428",
        )
        ds2 = cp_models.GrafanaDatasource(
            name="logs",
            project_id=PID,
            instance=inst,
            type="victoriametrics-logs-datasource",
            url="http://10.0.0.1:9428",
        )
        monkeypatch.setattr(inst, "get_datasources", lambda: [ds1, ds2])

        result = b._get_datasources(inst)

        assert len(result) == 2


class TestRenderNodeConfig:
    """Tests for the infra builder's ``_render_node_configs`` env file."""

    @pytest.fixture
    def infra_builder(self, monkeypatch):
        from exordos_observability.common import builder as common_builder
        from exordos_observability.grafana.controlplane.infra.services import (
            builder as infra_builder_mod,
        )

        # Avoid real Core API connection — _render_node_configs doesn't
        # need the client.
        monkeypatch.setattr(common_builder, "create_core_client", lambda **kw: None)
        return infra_builder_mod.CoreInfraBuilder(
            core_username="u",
            core_password="p",
            core_api_base_url="http://localhost",
            project_id=PID,
        )

    def _make_instance(self, **kwargs):
        from exordos_observability.grafana.controlplane.infra.dm import (
            models as infra_models,
        )

        kwargs.setdefault("name", "i")
        kwargs.setdefault("project_id", PID)
        kwargs.setdefault("cpu", 1)
        kwargs.setdefault("ram", 512)
        kwargs.setdefault("root_disk_size", 8)
        kwargs.setdefault("version_ref", "x")
        kwargs.setdefault("auth", auth_kinds.PasswordAuth(password="secret"))
        return infra_models.GrafanaInstance(**kwargs)

    def _env(self, builder, inst):
        return inst._render_node_configs()[c.GRAFANA_ENV_FILE]

    def test_password_auth_renders_no_oidc_vars(self, infra_builder) -> None:
        inst = self._make_instance()  # PasswordAuth by default in fixture
        env = self._env(infra_builder, inst)
        assert "GF_AUTH_GENERIC_OAUTH" not in env
        assert "GF_SECURITY_ADMIN_PASSWORD=secret" in env
        assert "GF_SERVER_HTTP_PORT=3000" in env

    def test_oidc_auth_renders_oauth_vars(self, infra_builder) -> None:
        inst = self._make_instance(
            auth=auth_kinds.OidcAuth(
                client_id="grafana-oidc",
                client_secret="oidc-secret",
                auth_url="http://core.local/auth",
                token_url="http://core.local/token",
                api_url="http://core.local/userinfo",
                root_url="http://observability.local.genesis-core.tech",
                scopes="openid profile email",
            )
        )
        env = self._env(infra_builder, inst)
        assert "GF_AUTH_GENERIC_OAUTH_ENABLED=true" in env
        assert "GF_AUTH_GENERIC_OAUTH_USE_OPENID_CONNECT=true" in env
        assert "GF_AUTH_GENERIC_OAUTH_NAME=Exordos" in env
        assert "GF_AUTH_GENERIC_OAUTH_CLIENT_ID=grafana-oidc" in env
        assert "GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=oidc-secret" in env
        assert "GF_AUTH_GENERIC_OAUTH_AUTH_URL=http://core.local/auth" in env
        assert "GF_AUTH_GENERIC_OAUTH_TOKEN_URL=http://core.local/token" in env
        assert "GF_AUTH_GENERIC_OAUTH_API_URL=http://core.local/userinfo" in env
        assert "GF_AUTH_GENERIC_OAUTH_SCOPES=openid profile email" in env
        assert "GF_AUTH_GENERIC_OAUTH_ALLOW_SIGN_UP=true" in env
        assert "GF_USERS_AUTO_ASSIGN_ORG_ROLE=Editor" in env
        assert "GF_SERVER_ROOT_URL=http://observability.local.genesis-core.tech" in env
        # Admin password is deterministically generated by the CP for OIDC.
        assert "GF_SECURITY_ADMIN_PASSWORD=" in env

    def test_oidc_admin_password_is_deterministic(self, infra_builder) -> None:
        """Repeated renders with the same instance produce the same password."""
        inst = self._make_instance(
            auth=auth_kinds.OidcAuth(
                client_id="grafana-oidc",
                client_secret="s3cret",
                auth_url="http://core.local/auth",
                token_url="http://core.local/token",
                api_url="http://core.local/userinfo",
                root_url="http://observability.local.genesis-core.tech",
            )
        )
        env1 = self._env(infra_builder, inst)
        env2 = self._env(infra_builder, inst)
        # Extract the password line from both renders.
        pw1 = [
            line for line in env1.splitlines() if "GF_SECURITY_ADMIN_PASSWORD=" in line
        ][0]
        pw2 = [
            line for line in env2.splitlines() if "GF_SECURITY_ADMIN_PASSWORD=" in line
        ][0]
        assert pw1 == pw2
