"""Document processing overrides and source-based parent contexts."""
from alembic import op

revision='kb0014'
down_revision='kb0013'
branch_labels=None
depends_on=None


def upgrade():
    # Existing source IDs, owner policies, revisions and indexes remain intact.
    op.execute('ALTER TABLE public.kb_editions ADD COLUMN processing_config jsonb')
    op.execute('ALTER TABLE public.kb_editions ADD COLUMN processing_version integer NOT NULL DEFAULT 0 CHECK(processing_version>=0)')
    op.execute("ALTER TABLE public.kb_chunks ADD COLUMN parent_spans jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(parent_spans)='array')")


def downgrade():
    op.drop_column('kb_chunks','parent_spans',schema='public')
    op.drop_column('kb_editions','processing_version',schema='public')
    op.drop_column('kb_editions','processing_config',schema='public')
