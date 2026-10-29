"""Trusted portal process configuration; never selected by an HTTP parameter."""
import os
import re

SHARED_TABLES = frozenset('''kb_libraries kb_works kb_editions kb_assets kb_edition_assets
kb_pages kb_page_labels kb_revisions kb_blocks kb_indexes kb_chunks kb_reference_candidates
kb_corpora kb_core_books kb_core_files kb_core_passages kb_literature_category_state
kb_literature_categories kb_literature_memberships kb_document_library_state
kb_document_groups kb_document_entries kb_schema_version'''.split())


def private_schema():
    value = os.environ.get('SHIXUE_PRIVATE_SCHEMA', '')
    if value and not re.fullmatch(r'w_[a-f0-9]{32}', value):
        raise RuntimeError('个人数据分区配置无效')
    return value


def library_key(fallback):
    return os.environ.get('SHIXUE_LIBRARY_KEY') or fallback


def read_only_library():
    return bool(private_schema())
