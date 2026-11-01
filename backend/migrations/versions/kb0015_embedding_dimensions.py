"""Allow different embedding dimensions while keeping old indexes readable."""
from alembic import op
revision='kb0015'
down_revision='kb0014'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('DROP INDEX IF EXISTS public.kb_chunks_vectors')
    op.execute('ALTER TABLE public.kb_chunks ALTER COLUMN embedding TYPE vector')
    # Expression indexes use a bounded set of common dimensions. Other supported
    # dimensions use an exact distance scan, never padding or slicing vectors.
    for dimensions in (384,512,768,1024,1536,2048,3072):
        kind='vector' if dimensions<=2000 else 'halfvec'
        op.execute(f'CREATE INDEX kb_chunks_vectors_{dimensions} ON public.kb_chunks USING hnsw ((embedding::{kind}({dimensions})) {kind}_cosine_ops) WHERE vector_dims(embedding)={dimensions}')


def downgrade():
    raise RuntimeError('变维向量不能自动改回 384 维，请使用完整备份恢复')
