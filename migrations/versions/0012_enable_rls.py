"""Turn on Row-Level Security for every table in the public schema.

Supabase exposes the public schema through its REST API, so without RLS anyone
holding the project URL and anon key can read or change every row.  With RLS on
and no policies, the API roles (anon, authenticated) see nothing.  Job Buddy is
unaffected: it connects directly as the table owner (postgres), which bypasses RLS.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in sa.inspect(bind).get_table_names(schema="public"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in sa.inspect(bind).get_table_names(schema="public"):
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
