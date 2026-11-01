"""System literature and account-owned literature share stable source IDs.

The authenticated database role, rather than a request parameter, owns private
rows. Research FKs continue to reference both kinds of source in public tables.
"""
from alembic import op
import sqlalchemy as sa

revision = 'kb0013'
down_revision = 'kb0012'
branch_labels = None
depends_on = None

TABLES = '''kb_libraries kb_works kb_editions kb_assets kb_edition_assets kb_pages
kb_page_labels kb_revisions kb_blocks kb_indexes kb_chunks kb_reference_candidates
kb_corpora kb_core_books kb_core_files kb_core_passages kb_literature_category_state
kb_literature_categories kb_literature_memberships kb_document_library_state
kb_document_groups kb_document_entries'''.split()


def upgrade():
    op.execute("""CREATE FUNCTION public.kb_document_owner() RETURNS text
      LANGUAGE sql STABLE AS $$ SELECT CASE WHEN current_user ~ '^wj_r_[0-9a-f]{32}$'
        THEN current_user::text ELSE NULL::text END $$""")
    inspector = sa.inspect(op.get_bind())
    foreign_keys = {table: inspector.get_foreign_keys(table, schema='public') for table in TABLES}
    for table in TABLES:
        # Existing rows are system-provided, including all preserved source IDs.
        op.execute(f'ALTER TABLE public.{table} ADD COLUMN owner_role text')
        op.execute(f'ALTER TABLE public.{table} ALTER COLUMN owner_role SET DEFAULT public.kb_document_owner()')
        op.execute(f'CREATE INDEX {table}_owner ON public.{table}(owner_role)')
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY kb_read ON public.{table} FOR SELECT
          USING (owner_role IS NULL OR owner_role=current_user::text)""")
        for command in ('INSERT', 'UPDATE', 'DELETE'):
            condition = 'owner_role IS NOT DISTINCT FROM public.kb_document_owner()'
            clauses = (f'USING ({condition}) ' if command != 'INSERT' else '') + (f'WITH CHECK ({condition})' if command != 'DELETE' else '')
            op.execute(f'CREATE POLICY kb_{command.lower()} ON public.{table} FOR {command} {clauses}')
    op.execute('ALTER TABLE public.kb_assets DROP CONSTRAINT kb_asset_sha256')
    op.execute('ALTER TABLE public.kb_assets ADD CONSTRAINT kb_asset_sha256 UNIQUE NULLS NOT DISTINCT(owner_role,sha256)')
    # A private row cannot attach itself to a public edition or another owner's
    # asset. Real FKs remain authoritative; the trigger also checks ownership.
    for table in TABLES:
        checks = []
        for fk in foreign_keys[table]:
            target = fk['referred_table']
            if target not in TABLES:
                continue
            columns = fk['constrained_columns']
            present = ' AND '.join(f'NEW.{column} IS NOT NULL' for column in columns)
            match = ' AND '.join(f'p.{remote}=NEW.{local}' for local, remote in zip(columns, fk['referred_columns']))
            checks.append(f"""IF {present} AND NOT EXISTS(SELECT 1 FROM public.{target} p
              WHERE {match} AND p.owner_role IS NOT DISTINCT FROM NEW.owner_role) THEN
              RAISE EXCEPTION 'Document ownership mismatch' USING ERRCODE='42501'; END IF;""")
        if checks:
            op.execute(f"CREATE FUNCTION public.{table}_ownership() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN " + '\n'.join(checks) + ' RETURN NEW; END $$')
            op.execute(f'CREATE TRIGGER kb_ownership BEFORE INSERT OR UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{table}_ownership()')


def downgrade():
    raise RuntimeError('个人文献不能自动改为公开，请通过完整备份恢复')
