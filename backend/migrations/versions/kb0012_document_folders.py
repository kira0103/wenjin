"""Import groups and original file paths; editions and originals remain authoritative."""
from alembic import op

revision='kb0012'
down_revision='kb0011'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''
    CREATE TABLE kb_document_library_state (
      workspace_key text PRIMARY KEY, revision integer NOT NULL DEFAULT 0, initialized boolean NOT NULL DEFAULT false
    );
    CREATE TABLE kb_document_groups (
      id uuid PRIMARY KEY, workspace_key text NOT NULL, name text NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100),
      version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX kb_document_groups_name ON kb_document_groups(workspace_key,lower(name));
    CREATE TABLE kb_document_entries (
      id uuid PRIMARY KEY, workspace_key text NOT NULL,
      group_id uuid REFERENCES kb_document_groups(id) ON DELETE SET NULL,
      edition_id uuid NOT NULL REFERENCES kb_editions(id), asset_id uuid REFERENCES kb_assets(id),
      relative_path text NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 2000),
      origin_key text NOT NULL, version integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_key,origin_key)
    );
    CREATE INDEX kb_document_entries_group ON kb_document_entries(workspace_key,group_id,relative_path);
    CREATE INDEX kb_document_entries_edition ON kb_document_entries(edition_id,asset_id);
    CREATE TABLE kb_document_imports (
      id uuid PRIMARY KEY, workspace_key text NOT NULL, selection jsonb NOT NULL,
      manifest jsonb NOT NULL, state text NOT NULL DEFAULT 'preview', group_id uuid REFERENCES kb_document_groups(id) ON DELETE SET NULL,
      item_type text NOT NULL DEFAULT 'article', job_id uuid REFERENCES kb_jobs(id),
      result jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX kb_document_imports_workspace ON kb_document_imports(workspace_key,created_at);
    ''')


def downgrade():
    raise RuntimeError('不自动移除文献目录记录')
