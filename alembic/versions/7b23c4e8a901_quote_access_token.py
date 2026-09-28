"""protect quote and order operations with quote access tokens

Revision ID: 7b23c4e8a901
Revises: 9336ebdc3898
"""

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "7b23c4e8a901"
down_revision = "9336ebdc3898"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quotes", sa.Column("access_token", sa.String(64), nullable=True))
    connection = op.get_bind()
    quote_ids = connection.execute(sa.text("SELECT id FROM quotes")).scalars()
    for quote_id in quote_ids:
        connection.execute(
            sa.text("UPDATE quotes SET access_token = :token WHERE id = :id"),
            {"token": uuid4().hex + uuid4().hex, "id": quote_id},
        )
    with op.batch_alter_table("quotes") as batch:
        batch.alter_column("access_token", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint("uq_quotes_access_token", ["access_token"])


def downgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.drop_constraint("uq_quotes_access_token", type_="unique")
        batch.drop_column("access_token")
