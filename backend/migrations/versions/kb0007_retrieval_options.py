"""Workspace-scoped, backed-up retrieval settings with optimistic concurrency."""
from alembic import op

revision='kb0007'
down_revision='kb0006'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE TABLE kb_retrieval_settings (
        workspace_key text PRIMARY KEY, version integer NOT NULL DEFAULT 1,
        options jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(version>=1),CHECK(jsonb_typeof(options)='object')
    );''')


def downgrade():
    raise RuntimeError('检索配置应保留，请使用成套备份恢复')
