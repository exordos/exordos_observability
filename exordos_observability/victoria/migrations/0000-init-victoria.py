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
        return "b7e1c2a0-3f4d-4a6b-9c8e-1d2f3a4b5c6d"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
            CREATE TABLE victoria_versions (
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
            CREATE TABLE victoria_instances (
                uuid UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT,
                project_id UUID NOT NULL,
                status VARCHAR(64) NOT NULL DEFAULT 'NEW',
                cpu INT NOT NULL CHECK (cpu BETWEEN 1 AND 128),
                ram INT NOT NULL CHECK (ram BETWEEN 512 AND 1073741824),
                metrics_disk_size INT NOT NULL CHECK (metrics_disk_size BETWEEN 8 AND 1073741824),
                logs_disk_size INT NOT NULL CHECK (logs_disk_size BETWEEN 8 AND 1073741824),
                retention_period VARCHAR(16) NOT NULL DEFAULT '30d',
                replicas INT NOT NULL CHECK (replicas = 1),
                version_ref VARCHAR(4096) NOT NULL,
                "ipsv4" VARCHAR(15) ARRAY,
                metrics_endpoint VARCHAR(512) NOT NULL DEFAULT '',
                logs_endpoint VARCHAR(512) NOT NULL DEFAULT '',
                vmauth JSONB NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );

            CREATE INDEX ON victoria_instances(project_id, name);
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        tables = [
            "victoria_instances",
            "victoria_versions",
        ]

        for table in tables:
            self._delete_table_if_exists(session, table)


migration_step = MigrationStep()
