"""pricing rules and quote revisions

P1 商务能力：新增价格规则库表，并为报价表补齐折扣与版本谱系字段。

Revision ID: eedbdc10eaf7
Revises: 1ec9685aa17c
Create Date: 2026-10-06 19:06:34.202077
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "eedbdc10eaf7"
down_revision: str | None = "1ec9685aa17c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 约束显式命名：autogenerate 生成的是 None，drop 时会失败。
FK_SUPERSEDED = "fk_quotes_superseded_by_id"
FK_ROOT = "fk_quotes_root_quote_id"


def upgrade() -> None:
    op.create_table(
        "pricing_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("condition", mysql.JSON(), nullable=False),
        sa.Column("action", mysql.JSON(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pricing_rules_code"), "pricing_rules", ["code"], unique=True)
    op.create_index(op.f("ix_pricing_rules_kind"), "pricing_rules", ["kind"], unique=False)
    op.create_index(op.f("ix_pricing_rules_priority"), "pricing_rules", ["priority"], unique=False)
    op.create_index(op.f("ix_pricing_rules_status"), "pricing_rules", ["status"], unique=False)

    # discount 为 NOT NULL：存量报价行必须有值，否则 MySQL 会以
    # 「无默认值」拒绝加列。server_default 保证历史数据填充为 0（旧定价无折扣）。
    op.add_column(
        "quotes",
        sa.Column(
            "discount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column("quotes", sa.Column("root_quote_id", sa.Integer(), nullable=True))
    op.add_column("quotes", sa.Column("superseded_by_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_quotes_root_quote_id"), "quotes", ["root_quote_id"], unique=False)

    # SQLite 不支持 ALTER 添加约束（需 batch mode 的拷表重建），而 CI 以 SQLite
    # 校验迁移可用性。外键在 MySQL 上建立；SQLite 下跳过——它本就不强制外键，
    # 且该差异不影响生产行为。列与索引在两种方言上都会创建。
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(FK_SUPERSEDED, "quotes", "quotes", ["superseded_by_id"], ["id"])
        op.create_foreign_key(FK_ROOT, "quotes", "quotes", ["root_quote_id"], ["id"])

    # 存量报价补齐自指：首版 root_quote_id 指向自身，与 create_quote 的写入语义一致。
    op.execute("UPDATE quotes SET root_quote_id = id WHERE root_quote_id IS NULL")


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(FK_ROOT, "quotes", type_="foreignkey")
        op.drop_constraint(FK_SUPERSEDED, "quotes", type_="foreignkey")
    op.drop_index(op.f("ix_quotes_root_quote_id"), table_name="quotes")
    op.drop_column("quotes", "superseded_by_id")
    op.drop_column("quotes", "root_quote_id")
    op.drop_column("quotes", "discount")
    op.drop_index(op.f("ix_pricing_rules_status"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_priority"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_kind"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_code"), table_name="pricing_rules")
    op.drop_table("pricing_rules")
