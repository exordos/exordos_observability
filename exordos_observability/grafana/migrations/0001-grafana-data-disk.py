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
        self._depends = ["0000-init-grafana.py"]

    @property
    def migration_id(self):
        return "ef1b4f04-7ed6-4946-8ff8-e8e9a47fca11"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute("""
            ALTER TABLE grafana_instances
            ADD COLUMN data_disk_size INT NOT NULL DEFAULT 10
                CHECK (data_disk_size BETWEEN 8 AND 1073741824);
        """)

    def downgrade(self, session):
        session.execute("ALTER TABLE grafana_instances DROP COLUMN data_disk_size;")


migration_step = MigrationStep()
