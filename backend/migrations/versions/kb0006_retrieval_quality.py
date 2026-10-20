"""Durable retrieval diagnostics and source-anchored evaluation baselines."""
from alembic import op

revision = 'kb0006'
down_revision = 'kb0005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
      CREATE TABLE kb_retrieval_runs (
        id uuid PRIMARY KEY, workspace_key text NOT NULL,
        query text NOT NULL, mode text NOT NULL, payload jsonb NOT NULL,
        snapshot jsonb NOT NULL DEFAULT '[]', diagnostics jsonb NOT NULL,
        results jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE INDEX kb_retrieval_runs_recent ON kb_retrieval_runs(workspace_key,created_at DESC);
      CREATE TABLE kb_eval_suites (
        id uuid PRIMARY KEY, workspace_key text NOT NULL, name text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE kb_eval_cases (
        id uuid PRIMARY KEY, suite_id uuid NOT NULL REFERENCES kb_eval_suites(id),
        search jsonb NOT NULL, expected jsonb NOT NULL, expect_empty boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(jsonb_typeof(expected)='array'),
        CHECK((expect_empty AND jsonb_array_length(expected)=0) OR
              (NOT expect_empty AND jsonb_array_length(expected)>0))
      );
      CREATE TABLE kb_eval_runs (
        id uuid PRIMARY KEY, suite_id uuid NOT NULL REFERENCES kb_eval_suites(id),
        job_id uuid NOT NULL REFERENCES kb_jobs(id), case_snapshot jsonb NOT NULL,
        report jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
      );
      ALTER TABLE kb_qa_runs ADD COLUMN retrieval_run_id uuid REFERENCES kb_retrieval_runs(id);
    ''')


def downgrade():
    raise RuntimeError('评测与诊断记录应保留，请使用成套备份恢复')
