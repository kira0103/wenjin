"""Workspace research categories organize works without copying editions."""
from alembic import op

revision = 'kb0011'
down_revision = 'kb0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE kb_literature_category_state (
      workspace_key text PRIMARY KEY, revision integer NOT NULL DEFAULT 0
    );
    CREATE TABLE kb_literature_categories (
      id uuid PRIMARY KEY, workspace_key text NOT NULL,
      parent_id uuid REFERENCES kb_literature_categories(id),
      name text NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 80),
      position integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX kb_literature_categories_workspace ON kb_literature_categories(workspace_key,parent_id,position);
    CREATE TABLE kb_literature_memberships (
      category_id uuid NOT NULL REFERENCES kb_literature_categories(id) ON DELETE CASCADE,
      work_id uuid NOT NULL REFERENCES kb_works(id),
      PRIMARY KEY(category_id,work_id)
    );
    CREATE INDEX kb_literature_memberships_work ON kb_literature_memberships(work_id,category_id);
    ''')


def downgrade():
    raise RuntimeError('请通过完整备份恢复文献分类')
