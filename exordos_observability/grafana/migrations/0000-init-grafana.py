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

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = []

    @property
    def migration_id(self):
        return "c4f2a1b3-6d5e-4f7a-8b9c-2e3f4a5b6c7d"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
            CREATE TABLE grafana_versions (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                description TEXT,
                image TEXT,
                version_ref VARCHAR(4096) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            """,
            """
            CREATE TABLE grafana_instances (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                status VARCHAR(64) NOT NULL DEFAULT 'NEW',
                cpu INT NOT NULL CHECK (cpu BETWEEN 1 AND 128),
                ram INT NOT NULL CHECK (ram BETWEEN 512 AND 1073741824),
                root_disk_size INT NOT NULL CHECK (root_disk_size BETWEEN 8 AND 1073741824),
                auth JSONB NOT NULL,
                replicas INT NOT NULL DEFAULT 1 CHECK (replicas = 1),
                version_ref VARCHAR(4096) NOT NULL,
                "ipsv4" VARCHAR(15) ARRAY,
                ui_url VARCHAR(512) NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE INDEX ON grafana_instances(project_id, name);
            """,
            """
            CREATE TABLE grafana_datasources (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                instance UUID NOT NULL,
                status VARCHAR(64) NOT NULL DEFAULT 'NEW',
                type VARCHAR(32) NOT NULL,
                url VARCHAR(512) NOT NULL,
                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                auth JSONB,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                FOREIGN KEY (instance) REFERENCES grafana_instances(uuid)
            );

            CREATE INDEX IF NOT EXISTS grafana_datasources_project_id_idx
                ON grafana_datasources (project_id);
            """,
            """
            CREATE TABLE grafana_dashboards (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                source JSONB NOT NULL,
                version_ref VARCHAR(4096) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE INDEX IF NOT EXISTS grafana_dashboards_project_id_idx
                ON grafana_dashboards (project_id);
            """,
            """
            CREATE TABLE grafana_instance_dashboards (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                instance UUID NOT NULL,
                status VARCHAR(64) NOT NULL DEFAULT 'NEW',
                folder VARCHAR(255) NOT NULL DEFAULT '',
                version_ref VARCHAR(4096) NOT NULL,
                saved_version_ref VARCHAR(4096),
                content JSONB,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                FOREIGN KEY (instance) REFERENCES grafana_instances(uuid)
            );

            CREATE INDEX IF NOT EXISTS grafana_instance_dashboards_project_id_idx
                ON grafana_instance_dashboards (project_id);
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        tables = [
            "grafana_instance_dashboards",
            "grafana_dashboards",
            "grafana_datasources",
            "grafana_instances",
            "grafana_versions",
        ]

        for table in tables:
            self._delete_table_if_exists(session, table)


migration_step = MigrationStep()
