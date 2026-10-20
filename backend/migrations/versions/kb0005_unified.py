"""Single catalog authority, scoped project sources and durable activation."""
from alembic import op

revision = 'kb0005'
down_revision = 'kb0004'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
      ALTER TABLE kb_works ADD COLUMN version integer NOT NULL DEFAULT 1;
      ALTER TABLE kb_editions ADD COLUMN version integer NOT NULL DEFAULT 1;
      CREATE TABLE kb_project_sources (
        id uuid PRIMARY KEY, project_id text NOT NULL, edition_id uuid NOT NULL REFERENCES kb_editions(id),
        roles jsonb NOT NULL DEFAULT '["reference"]', witness_label text NOT NULL DEFAULT '',
        scope_note text NOT NULL DEFAULT '', mode text NOT NULL DEFAULT 'edition',
        ranges jsonb NOT NULL DEFAULT '[]', range_key text NOT NULL,
        state text NOT NULL DEFAULT 'active', version integer NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(mode IN ('edition','ranges')), CHECK(state IN ('active','archived','invalid')),
        CHECK(jsonb_typeof(ranges)='array'), CHECK(jsonb_typeof(roles)='array'),
        UNIQUE(project_id,edition_id,range_key)
      );
      CREATE INDEX kb_project_sources_project ON kb_project_sources(project_id,state);
      CREATE TABLE kb_reference_candidates (
        id text PRIMARY KEY, source_edition_id uuid NOT NULL REFERENCES kb_editions(id),
        source_asset_id uuid REFERENCES kb_assets(id), page_index integer,
        ordinal integer NOT NULL DEFAULT 0, raw_text text NOT NULL, title text NOT NULL,
        authors_raw text NOT NULL DEFAULT '', year_label text NOT NULL DEFAULT '',
        matched_edition_id uuid REFERENCES kb_editions(id), match_status text NOT NULL DEFAULT '待核验',
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE kb_catalog_activation (
        id integer PRIMARY KEY CHECK(id=1), epoch uuid NOT NULL,
        state text NOT NULL DEFAULT 'prepared', report jsonb NOT NULL DEFAULT '{}',
        backup_id text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(state IN ('prepared','migrating','verified','activated'))
      );
      CREATE TABLE kb_migration_items (
        entry_id text PRIMARY KEY, edition_id uuid REFERENCES kb_editions(id),
        state text NOT NULL DEFAULT 'migrated', archive jsonb NOT NULL DEFAULT '{}', error text NOT NULL DEFAULT '',
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(state IN ('migrated','verified','conflict','failed','excluded','missing_original'))
      );
    ''')


def downgrade():
    raise RuntimeError('统一文献库不可自动删表回退，请恢复成套备份')
