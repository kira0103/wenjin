"""Durable, workspace-scoped research runs and bounded step records."""
from alembic import op

revision='kb0008'
down_revision='kb0007'
branch_labels=None
depends_on=None

def upgrade():
    op.execute('''CREATE TABLE kb_research_runs (
      id uuid PRIMARY KEY, workspace_key text NOT NULL,
      job_id uuid NOT NULL UNIQUE REFERENCES kb_jobs(id), question text NOT NULL,
      request jsonb NOT NULL, snapshot jsonb NOT NULL, model jsonb NOT NULL,
      evidence jsonb NOT NULL DEFAULT '[]', answer text NOT NULL DEFAULT '',
      warnings jsonb NOT NULL DEFAULT '[]', usage jsonb NOT NULL DEFAULT '{}',
      verified boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX kb_research_workspace ON kb_research_runs(workspace_key,created_at DESC);
    CREATE TABLE kb_research_steps (
      run_id uuid NOT NULL REFERENCES kb_research_runs(id), sequence integer NOT NULL,
      action text NOT NULL, arguments jsonb NOT NULL DEFAULT '{}',
      summary text NOT NULL DEFAULT '', state text NOT NULL DEFAULT 'running',
      retrieval_run_id uuid REFERENCES kb_retrieval_runs(id),
      created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
      PRIMARY KEY(run_id,sequence), CHECK(sequence>0),
      CHECK(state IN ('running','completed','interrupted','failed'))
    );''')

def downgrade():
    raise RuntimeError('研究过程应保留，请使用成套备份恢复')
