"""Preserve bibliography-only legacy entries as well as attached materials."""
from alembic import op
revision='kb0004'
down_revision='kb0003'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE kb_legacy_entries(entry_id text PRIMARY KEY,edition_id uuid NOT NULL REFERENCES kb_editions(id),created_at timestamptz NOT NULL DEFAULT now());
    INSERT INTO kb_legacy_entries(entry_id,edition_id) SELECT DISTINCT ON(entry_id) entry_id,edition_id FROM kb_legacy_links ORDER BY entry_id,created_at;
    """)

def downgrade():raise RuntimeError('请使用备份恢复')
