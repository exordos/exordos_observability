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
from exordos_observability.grafana.controlplane.dm import auth, models


class TestGrafanaVersion:
    def test_tablename(self) -> None:
        assert models.GrafanaVersion.__tablename__ == "grafana_versions"

    def test_version_ref_is_stored_field(self) -> None:
        """version_ref must be a stored property, not a computed @property,
        so the core-agent includes it in actual_resource.value."""
        assert "version_ref" in models.GrafanaVersion.properties

    def test_build_version_ref_with_http_image(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img = "https://repo.example.com/img.raw.zst"
        ref = build_version_ref("grafana", str(uid), img)
        assert ref == f"urn:exordos:grafana:{uid}:{img}"

    def test_build_version_ref_with_urn_image_extracts_uuid(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img_uuid = "3cd45e03-7925-5ca1-9eec-9bd481f94a89"
        img = f"urn:images:{img_uuid}"
        ref = build_version_ref("grafana", str(uid), img)
        assert ref == f"urn:exordos:grafana:{uid}:{img_uuid}"

    def test_build_version_ref_round_trips_through_parse(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img = "https://repo.example.com/img.raw.zst"
        ref = build_version_ref("grafana", str(uid), img)
        slug, parsed_uuid, parsed_image = parse_version_ref(ref)
        assert slug == "grafana"
        assert parsed_uuid == str(uid)
        assert parsed_image == img
        assert parse_image(ref) == img

    def test_build_version_ref_changes_when_image_changes(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        ref1 = build_version_ref(
            "grafana", str(uid), "https://repo.example.com/img1.raw.zst"
        )
        ref2 = build_version_ref(
            "grafana", str(uid), "https://repo.example.com/img2.raw.zst"
        )
        assert ref1 != ref2

    def test_build_version_ref_same_for_same_uuid_and_image(self) -> None:
        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        img = "https://repo.example.com/img.raw.zst"
        assert build_version_ref("grafana", str(uid), img) == build_version_ref(
            "grafana", str(uid), img
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


class TestGrafanaInstance:
    def test_tablename(self) -> None:
        assert models.GrafanaInstance.__tablename__ == "grafana_instances"

    def test_status_values(self) -> None:
        values = [s.value for s in models.GrafanaStatus]
        assert "NEW" in values
        assert "IN_PROGRESS" in values
        assert "ACTIVE" in values
        assert "ERROR" in values

    def test_auth_is_required_without_default(self) -> None:
        with pytest.raises(Exception):
            models.GrafanaInstance(
                name="i",
                project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
                cpu=1,
                ram=512,
                root_disk_size=8,
                version_ref="x",
            )

    def test_replicas_is_stored_property(self) -> None:
        assert "replicas" in models.GrafanaInstance.properties

    def test_replicas_defaults_to_one(self) -> None:
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth.PasswordAuth(password="x"),
        )
        assert inst.replicas == 1

    def test_replicas_locked_to_single_node(self) -> None:
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            replicas=1,
            version_ref="x",
            auth=auth.PasswordAuth(password="x"),
        )
        assert inst.replicas == 1

        with pytest.raises(Exception):
            models.GrafanaInstance(
                name="i",
                project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
                cpu=1,
                ram=512,
                root_disk_size=8,
                replicas=2,
                version_ref="x",
                auth=auth.PasswordAuth(password="x"),
            )


class TestGrafanaDatasource:
    def test_tablename(self) -> None:
        assert models.GrafanaDatasource.__tablename__ == "grafana_datasources"

    def test_type_values(self) -> None:
        values = [t.value for t in models.GrafanaDatasourceType]
        assert values == ["prometheus", "victoriametrics-logs-datasource"]


class TestAuthMethod:
    def test_password_auth_kind(self) -> None:
        assert auth.PasswordAuth.KIND == "password"
        pw = auth.PasswordAuth(password="x")
        assert pw.kind == "password"
        assert pw.password == "x"

    def test_oidc_auth_deserializes(self) -> None:
        oidc = auth.OidcAuth(
            client_id="grafana-oidc",
            client_secret="s3cret",
            auth_url="http://core.local/auth",
            token_url="http://core.local/token",
            api_url="http://core.local/userinfo",
            root_url="http://observability.local.genesis-core.tech",
            scopes="openid profile email",
        )
        assert oidc.kind == "oidc"
        assert oidc.client_id == "grafana-oidc"
        assert oidc.client_secret == "s3cret"
        assert oidc.auth_url == "http://core.local/auth"
        assert oidc.token_url == "http://core.local/token"
        assert oidc.api_url == "http://core.local/userinfo"
        assert oidc.root_url == "http://observability.local.genesis-core.tech"
        assert oidc.scopes == "openid profile email"

    def test_oidc_auth_scopes_defaults(self) -> None:
        oidc = auth.OidcAuth(
            client_id="grafana-oidc",
            client_secret="s3cret",
            auth_url="http://core.local/auth",
            token_url="http://core.local/token",
            api_url="http://core.local/userinfo",
            root_url="http://observability.local.genesis-core.tech",
        )
        assert oidc.scopes == "openid profile email"

    def test_auth_accepts_password(self) -> None:
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth.PasswordAuth(password="x"),
        )
        assert isinstance(inst.auth, auth.PasswordAuth)
        assert inst.auth.password == "x"

    def test_auth_accepts_oidc(self) -> None:
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth.OidcAuth(
                client_id="grafana-oidc",
                client_secret="s3cret",
                auth_url="http://core.local/auth",
                token_url="http://core.local/token",
                api_url="http://core.local/userinfo",
                root_url="http://observability.local.genesis-core.tech",
            ),
        )
        assert isinstance(inst.auth, auth.OidcAuth)
        assert inst.auth.client_id == "grafana-oidc"

    def test_auth_is_stored_property(self) -> None:
        assert "auth" in models.GrafanaInstance.properties


class TestGrafanaDashboard:
    def test_tablename(self) -> None:
        assert models.GrafanaDashboard.__tablename__ == "grafana_instance_dashboards"

    def test_version_ref_is_stored_property(self) -> None:
        assert "version_ref" in models.GrafanaDashboard.properties

    def test_saved_version_ref_is_stored_property(self) -> None:
        assert "saved_version_ref" in models.GrafanaDashboard.properties

    def test_content_is_stored_property(self) -> None:
        assert "content" in models.GrafanaDashboard.properties

    def test_is_instance_child(self) -> None:
        assert issubclass(models.GrafanaDashboard, models.InstanceChildModel)

    def test_needs_content_refresh_when_saved_is_none(self) -> None:
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth.PasswordAuth(password="x"),
        )
        dash = models.GrafanaDashboard(
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            instance=inst,
            version_ref="abc-123_https://x.com/d.json",
        )
        assert dash.needs_content_refresh() is True

    def test_needs_content_refresh_when_version_ref_changed(self) -> None:
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth.PasswordAuth(password="x"),
        )
        dash = models.GrafanaDashboard(
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            instance=inst,
            version_ref="abc-123_https://x.com/d.json",
            saved_version_ref="abc-123_https://x.com/old.json",
        )
        assert dash.needs_content_refresh() is True

    def test_needs_content_refresh_when_up_to_date(self) -> None:
        ref = "abc-123_https://x.com/d.json"
        inst = models.GrafanaInstance(
            name="i",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            cpu=1,
            ram=512,
            root_disk_size=8,
            version_ref="x",
            auth=auth.PasswordAuth(password="x"),
        )
        dash = models.GrafanaDashboard(
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            instance=inst,
            version_ref=ref,
            saved_version_ref=ref,
        )
        assert dash.needs_content_refresh() is False


class TestGrafanaArtifactDashboard:
    def test_tablename(self) -> None:
        assert models.GrafanaArtifactDashboard.__tablename__ == "grafana_dashboards"

    def test_source_is_stored_property(self) -> None:
        assert "source" in models.GrafanaArtifactDashboard.properties

    def test_version_ref_is_stored_property(self) -> None:
        assert "version_ref" in models.GrafanaArtifactDashboard.properties

    def test_source_deserializes_to_urn_source(self) -> None:
        from exordos_observability.grafana.controlplane.dm import sources

        urn = "urn:artifacts:12345678-1234-1234-1234-123456789012"
        dash = models.GrafanaArtifactDashboard(
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=sources.UrnDashboardSource(urn=urn),
        )
        assert isinstance(dash.source, sources.UrnDashboardSource)
        assert dash.source.urn == urn

    def test_source_digest_for_urn(self) -> None:
        from exordos_observability.grafana.controlplane.dm import sources

        urn = "urn:artifacts:12345678-1234-1234-1234-123456789012"
        dash = models.GrafanaArtifactDashboard(
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=sources.UrnDashboardSource(urn=urn),
        )
        assert dash.source.source_digest() == urn

    def test_source_digest_for_raw(self) -> None:
        import hashlib
        import json

        from exordos_observability.grafana.controlplane.dm import sources

        content = {"title": "foo", "panels": []}
        dash = models.GrafanaArtifactDashboard(
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=sources.RawDashboardSource(content=content),
        )
        expected = hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest()
        assert dash.source.source_digest() == expected

    def test_version_ref_format(self) -> None:
        from exordos_observability.grafana.controlplane.dm import sources

        uid = sys_uuid.UUID("12345678-1234-1234-1234-123456789012")
        urn = "urn:artifacts:87654321-4321-4321-4321-210987654321"
        dash = models.GrafanaArtifactDashboard(
            uuid=uid,
            name="foo",
            project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
            source=sources.UrnDashboardSource(urn=urn),
        )
        # insert() would hit the DB; verify the computed format directly.
        expected = f"{uid}_{urn}"
        assert dash.build_version_ref() == expected
