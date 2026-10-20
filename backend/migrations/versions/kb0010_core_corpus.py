"""Core corpora reuse catalog editions and immutable original blocks."""
from alembic import op

revision = 'kb0010'
down_revision = 'kb0009'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
    CREATE TABLE kb_corpora (
      id uuid PRIMARY KEY, slug text NOT NULL UNIQUE, name text NOT NULL,
      source_path text NOT NULL, source_revision text NOT NULL DEFAULT '',
      state text NOT NULL DEFAULT 'pending', statistics jsonb NOT NULL DEFAULT '{}',
      errors jsonb NOT NULL DEFAULT '[]', updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE kb_core_books (
      corpus_id uuid NOT NULL REFERENCES kb_corpora(id), source_key text NOT NULL,
      edition_id uuid NOT NULL UNIQUE REFERENCES kb_editions(id), category text NOT NULL,
      fingerprint text NOT NULL, imported_revision uuid NOT NULL REFERENCES kb_revisions(id),
      file_count integer NOT NULL, byte_count bigint NOT NULL, metadata jsonb NOT NULL,
      PRIMARY KEY(corpus_id,source_key)
    );
    CREATE INDEX kb_core_books_category ON kb_core_books(corpus_id,category,source_key);
    CREATE TABLE kb_core_files (
      revision_id uuid NOT NULL REFERENCES kb_revisions(id), source_path text NOT NULL,
      asset_id uuid NOT NULL REFERENCES kb_assets(id), title text NOT NULL,
      metadata jsonb NOT NULL, body_offset integer NOT NULL,
      PRIMARY KEY(revision_id,source_path)
    );
    CREATE TABLE kb_core_passages (
      id bigserial PRIMARY KEY, block_id uuid NOT NULL UNIQUE REFERENCES kb_blocks(id),
      corpus_id uuid NOT NULL REFERENCES kb_corpora(id), edition_id uuid NOT NULL REFERENCES kb_editions(id),
      revision_id uuid NOT NULL REFERENCES kb_revisions(id), category text NOT NULL,
      source_path text NOT NULL, source_start integer NOT NULL, primary_length integer NOT NULL,
      normalized text NOT NULL, terms integer[] NOT NULL
    );
    CREATE INDEX kb_core_passages_terms ON kb_core_passages USING gin(terms);
    CREATE INDEX kb_core_passages_scope ON kb_core_passages(corpus_id,category,id);
    CREATE INDEX kb_core_passages_edition ON kb_core_passages(edition_id,revision_id,id);
    ''')


def downgrade():
    raise RuntimeError('古籍原文与引用须成套恢复，请使用完整备份')
