"""Bind filesystem tasks to their owning data directory."""
from alembic import op
revision='kb0003'
down_revision='kb0002'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("ALTER TABLE kb_jobs ADD COLUMN workspace_key text NOT NULL DEFAULT ''; CREATE INDEX kb_jobs_workspace ON kb_jobs(workspace_key,state);")

def downgrade():raise RuntimeError('请使用备份恢复')
