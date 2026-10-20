"""Versioned instruction skills, agent profiles and durable collaboration nodes."""
from alembic import op

revision='kb0009'
down_revision='kb0008'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''
    CREATE TABLE kb_assistant_definitions (
      id uuid PRIMARY KEY, workspace_key text NOT NULL, kind text NOT NULL CHECK(kind IN ('skill','agent')),
      slug text NOT NULL, draft jsonb NOT NULL, edit_version integer NOT NULL DEFAULT 1,
      enabled boolean NOT NULL DEFAULT true, active_version uuid,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(workspace_key,kind,slug)
    );
    CREATE TABLE kb_assistant_versions (
      id uuid PRIMARY KEY, definition_id uuid NOT NULL REFERENCES kb_assistant_definitions(id),
      number integer NOT NULL, content jsonb NOT NULL, sha256 text NOT NULL,
      revoked boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(definition_id,number)
    );
    ALTER TABLE kb_assistant_definitions ADD FOREIGN KEY(active_version) REFERENCES kb_assistant_versions(id);
    CREATE TABLE kb_skill_tests (
      id uuid PRIMARY KEY, workspace_key text NOT NULL,
      skill_version_id uuid NOT NULL REFERENCES kb_assistant_versions(id),
      research_run_id uuid NOT NULL UNIQUE REFERENCES kb_research_runs(id), created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE kb_collaboration_runs (
      id uuid PRIMARY KEY, workspace_key text NOT NULL, job_id uuid NOT NULL UNIQUE REFERENCES kb_jobs(id),
      request jsonb NOT NULL, snapshot jsonb NOT NULL, budget integer NOT NULL,
      answer text NOT NULL DEFAULT '', evidence jsonb NOT NULL DEFAULT '[]', warnings jsonb NOT NULL DEFAULT '[]',
      outcome text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE kb_collaboration_nodes (
      id uuid PRIMARY KEY, collaboration_id uuid NOT NULL REFERENCES kb_collaboration_runs(id),
      node_key text NOT NULL, title text NOT NULL, dependencies jsonb NOT NULL,
      specification jsonb NOT NULL, state text NOT NULL DEFAULT 'pending',
      research_run_id uuid REFERENCES kb_research_runs(id), message text NOT NULL DEFAULT '',
      UNIQUE(collaboration_id,node_key)
    );
    CREATE INDEX kb_collaboration_workspace ON kb_collaboration_runs(workspace_key,created_at DESC);
    CREATE INDEX kb_collaboration_research_run ON kb_collaboration_nodes(research_run_id);
    CREATE INDEX kb_assistant_workspace ON kb_assistant_definitions(workspace_key,kind);
    CREATE UNIQUE INDEX kb_research_request_key ON kb_research_runs(workspace_key,(request->>'request_key')) WHERE request->>'request_key' IS NOT NULL;
    CREATE UNIQUE INDEX kb_collaboration_request_key ON kb_collaboration_runs(workspace_key,(request->>'request_key')) WHERE request->>'request_key' IS NOT NULL;
    CREATE FUNCTION kb_assistant_version_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Skill and agent releases are immutable'; END IF;
      IF (to_jsonb(NEW)-'revoked') IS DISTINCT FROM (to_jsonb(OLD)-'revoked') OR OLD.revoked AND NOT NEW.revoked THEN
        RAISE EXCEPTION 'Skill and agent releases are immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER kb_assistant_version_immutable BEFORE UPDATE OR DELETE ON kb_assistant_versions FOR EACH ROW EXECUTE FUNCTION kb_assistant_version_immutable();
    ''')


def downgrade():
    raise RuntimeError('技能版本与研究记录应保留，请使用成套备份恢复')
