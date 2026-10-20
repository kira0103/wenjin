"""Research, immutable source revisions, durable jobs and searchable evidence."""
from alembic import op

revision = 'kb0002'
down_revision = 'kb0001'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE kb_editions ADD COLUMN deleted boolean NOT NULL DEFAULT false;
ALTER TABLE kb_editions ADD COLUMN tags jsonb NOT NULL DEFAULT '[]';
ALTER TABLE kb_editions ADD COLUMN active_revision uuid;
CREATE TABLE kb_revisions (
 id uuid PRIMARY KEY, edition_id uuid NOT NULL REFERENCES kb_editions(id),
 source_kind text NOT NULL, parent_id uuid REFERENCES kb_revisions(id),
 created_at timestamptz NOT NULL DEFAULT now(), config jsonb NOT NULL DEFAULT '{}',
 UNIQUE(id,edition_id)
);
ALTER TABLE kb_editions ADD CONSTRAINT kb_active_revision FOREIGN KEY(active_revision,id) REFERENCES kb_revisions(id,edition_id);
ALTER TABLE kb_edition_assets ADD COLUMN filename text NOT NULL DEFAULT '';
ALTER TABLE kb_edition_assets ADD COLUMN position_kind text NOT NULL DEFAULT 'page';
CREATE TABLE kb_blocks (
 id uuid PRIMARY KEY, revision_id uuid NOT NULL REFERENCES kb_revisions(id),
 asset_id uuid NOT NULL, page_index integer NOT NULL, ordinal integer NOT NULL,
 kind text NOT NULL DEFAULT 'body', section_path text NOT NULL DEFAULT '',
 body text NOT NULL, quality jsonb NOT NULL DEFAULT '{}',
 FOREIGN KEY(asset_id,page_index) REFERENCES kb_pages(asset_id,page_index),
 UNIQUE(revision_id,ordinal)
);
CREATE INDEX kb_blocks_revision ON kb_blocks(revision_id,ordinal);
CREATE TABLE kb_indexes (
 id uuid PRIMARY KEY, revision_id uuid NOT NULL REFERENCES kb_revisions(id),
 model_key text NOT NULL DEFAULT '', state text NOT NULL DEFAULT 'keyword',
 created_at timestamptz NOT NULL DEFAULT now(), config jsonb NOT NULL DEFAULT '{}'
);
ALTER TABLE kb_editions ADD COLUMN active_index uuid REFERENCES kb_indexes(id);
CREATE TABLE kb_chunks (
 id uuid PRIMARY KEY, index_id uuid NOT NULL REFERENCES kb_indexes(id),
 block_id uuid NOT NULL REFERENCES kb_blocks(id), start_offset integer NOT NULL,
 end_offset integer NOT NULL, body text NOT NULL, normalized text NOT NULL,
 tokens tsvector NOT NULL, embedding vector(384),
 CHECK(start_offset>=0 AND end_offset>start_offset)
);
CREATE INDEX kb_chunks_index ON kb_chunks(index_id);
CREATE INDEX kb_chunks_tokens ON kb_chunks USING gin(tokens);
CREATE INDEX kb_chunks_normalized ON kb_chunks USING gin(normalized gin_trgm_ops);
CREATE INDEX kb_chunks_vectors ON kb_chunks USING hnsw(embedding vector_cosine_ops);
CREATE TABLE kb_anchors (
 id uuid PRIMARY KEY, edition_id uuid NOT NULL REFERENCES kb_editions(id),
 block_id uuid NOT NULL REFERENCES kb_blocks(id), start_offset integer NOT NULL,
 end_offset integer NOT NULL, quote text NOT NULL, quote_hash text NOT NULL,
 locator jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(start_offset>=0 AND end_offset>start_offset)
);
CREATE TABLE kb_notes (
 id uuid PRIMARY KEY, title text NOT NULL, note_type text NOT NULL DEFAULT 'reading',
 body text NOT NULL DEFAULT '', tags jsonb NOT NULL DEFAULT '[]',
 status text NOT NULL DEFAULT 'draft', version integer NOT NULL DEFAULT 1,
 deleted boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), tokens tsvector NOT NULL DEFAULT ''
);
CREATE INDEX kb_notes_tokens ON kb_notes USING gin(tokens);
CREATE TABLE kb_note_revisions (
 note_id uuid NOT NULL REFERENCES kb_notes(id), version integer NOT NULL,
 title text NOT NULL, body text NOT NULL, origin text NOT NULL DEFAULT 'human',
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(note_id,version)
);
CREATE TABLE kb_note_sources (
 note_id uuid NOT NULL REFERENCES kb_notes(id), anchor_id uuid NOT NULL REFERENCES kb_anchors(id),
 relation text NOT NULL DEFAULT 'citation', comment text NOT NULL DEFAULT '',
 PRIMARY KEY(note_id,anchor_id)
);
CREATE TABLE kb_topics (
 id uuid PRIMARY KEY, title text NOT NULL, question text NOT NULL DEFAULT '',
 description text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kb_topic_notes (topic_id uuid REFERENCES kb_topics(id),note_id uuid REFERENCES kb_notes(id),PRIMARY KEY(topic_id,note_id));
CREATE TABLE kb_topic_editions (topic_id uuid REFERENCES kb_topics(id),edition_id uuid REFERENCES kb_editions(id),PRIMARY KEY(topic_id,edition_id));
CREATE TABLE kb_note_relations (
 source_id uuid REFERENCES kb_notes(id), target_id uuid REFERENCES kb_notes(id),
 relation text NOT NULL, PRIMARY KEY(source_id,target_id,relation), CHECK(source_id<>target_id)
);
CREATE TABLE kb_project_editions (
 project_id text NOT NULL, edition_id uuid REFERENCES kb_editions(id), roles jsonb NOT NULL DEFAULT '["reference"]',
 PRIMARY KEY(project_id,edition_id)
);
CREATE TABLE kb_jobs (
 id uuid PRIMARY KEY, kind text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued', stage text NOT NULL DEFAULT 'queued',
 progress integer NOT NULL DEFAULT 0, result jsonb NOT NULL DEFAULT '{}', error text NOT NULL DEFAULT '',
 attempts integer NOT NULL DEFAULT 0, lease_until timestamptz, owner text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX kb_jobs_queue ON kb_jobs(state,created_at);
CREATE TABLE kb_legacy_links (
 material_id text PRIMARY KEY, entry_id text NOT NULL, edition_id uuid NOT NULL REFERENCES kb_editions(id),
 asset_id uuid NOT NULL REFERENCES kb_assets(id), source_digest text NOT NULL, state text NOT NULL DEFAULT 'verified',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kb_qa_runs (
 id uuid PRIMARY KEY, question text NOT NULL, answer text NOT NULL, evidence jsonb NOT NULL,
 scope jsonb NOT NULL, model text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kb_audit_events (id uuid PRIMARY KEY,event text NOT NULL,details jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE FUNCTION kb_immutable_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Published source revisions are immutable'; END $$;
CREATE TRIGGER kb_blocks_immutable BEFORE UPDATE OR DELETE ON kb_blocks FOR EACH ROW EXECUTE FUNCTION kb_immutable_source();
CREATE TRIGGER kb_revisions_immutable BEFORE UPDATE OR DELETE ON kb_revisions FOR EACH ROW EXECUTE FUNCTION kb_immutable_source();
""")


def downgrade():
    raise RuntimeError('请使用已验证的完整备份恢复，不自动删除研究数据')
