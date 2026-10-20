from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("请通过 python -m backend.knowledge.cli upgrade 执行知识库迁移")
context.configure(connection=connection, version_table="kb_schema_version",
                  version_table_schema="public", transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
