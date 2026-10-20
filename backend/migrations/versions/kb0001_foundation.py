"""Immutable foundation schema. Extensions are provisioned separately by admin."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "kb0001"
down_revision = None
branch_labels = None
depends_on = None


def identifier(name="id", **options):
    return sa.Column(name, UUID(as_uuid=True), **options)


def created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def string(name, default="", nullable=False):
    return sa.Column(name, sa.Text(), nullable=nullable, server_default=default)


def upgrade():
    op.create_table("kb_libraries", identifier(primary_key=True), string("name"), created(),
                    sa.CheckConstraint("length(trim(name)) > 0", name="kb_library_name"))
    op.create_table("kb_works", identifier(primary_key=True),
                    identifier("library_id", nullable=False), string("item_type", "other"),
                    string("title"), string("date_label"),
                    sa.Column("authors_json", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
                    sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
                    created(), sa.ForeignKeyConstraint(["library_id"], ["kb_libraries.id"]),
                    sa.CheckConstraint("length(trim(title)) > 0", name="kb_work_title"),
                    sa.CheckConstraint("jsonb_typeof(authors_json)='array'", name="kb_work_authors_array"))
    op.create_index("kb_works_library", "kb_works", ["library_id"])
    op.create_table("kb_editions", identifier(primary_key=True), identifier("work_id", nullable=False),
                    string("label"), string("publisher"),
                    sa.Column("publication_year", sa.Integer(), nullable=True), string("doi"),
                    string("source_note"), string("metadata_status", "pending"), created(),
                    sa.ForeignKeyConstraint(["work_id"], ["kb_works.id"]),
                    sa.CheckConstraint("metadata_status IN ('pending','confirmed')", name="kb_edition_metadata_status"))
    op.create_index("kb_editions_work", "kb_editions", ["work_id"])
    op.create_table("kb_assets", identifier(primary_key=True), string("sha256"), string("storage_key"),
                    string("mime_type"), sa.Column("size_bytes", sa.BigInteger(), nullable=False),
                    string("availability", "present"), created(),
                    sa.UniqueConstraint("sha256", name="kb_asset_sha256"),
                    sa.UniqueConstraint("storage_key", name="kb_asset_storage_key"),
                    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="kb_asset_digest"),
                    sa.CheckConstraint("size_bytes >= 0", name="kb_asset_size"),
                    sa.CheckConstraint("length(storage_key)>0 AND storage_key !~ '(^/|[\\\\:]|(^|/)\\.\\.(/|$))'", name="kb_asset_relative_key"),
                    sa.CheckConstraint("availability IN ('present','missing','pending')", name="kb_asset_availability"))
    op.create_table("kb_edition_assets", identifier("edition_id", primary_key=True),
                    identifier("asset_id", primary_key=True), string("role", "original"),
                    sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
                    sa.ForeignKeyConstraint(["edition_id"], ["kb_editions.id"]),
                    sa.ForeignKeyConstraint(["asset_id"], ["kb_assets.id"]),
                    sa.CheckConstraint("ordinal >= 0", name="kb_edition_asset_ordinal"))
    op.create_table("kb_pages", identifier("asset_id", primary_key=True),
                    sa.Column("page_index", sa.Integer(), primary_key=True), string("image_key"),
                    sa.ForeignKeyConstraint(["asset_id"], ["kb_assets.id"]),
                    sa.CheckConstraint("page_index >= 1", name="kb_physical_page_index"))
    op.create_table("kb_page_labels", identifier("edition_id", primary_key=True),
                    identifier("asset_id", primary_key=True),
                    sa.Column("page_index", sa.Integer(), primary_key=True),
                    string("printed_label"), string("volume_label"), string("leaf_label"), string("side"),
                    sa.ForeignKeyConstraint(["edition_id", "asset_id"],
                                            ["kb_edition_assets.edition_id", "kb_edition_assets.asset_id"]),
                    sa.ForeignKeyConstraint(["asset_id", "page_index"], ["kb_pages.asset_id", "kb_pages.page_index"]),
                    sa.CheckConstraint("side IN ('','recto','verso')", name="kb_page_side"))


def downgrade():
    raise RuntimeError("知识库不支持自动删表回退，请使用已验证的备份恢复")
