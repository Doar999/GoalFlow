"""T08 conversation and message contract.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36
_TIME = 32


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=True),
        sa.Column("task_id", sa.String(_ID), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(_TIME), nullable=False),
        sa.Column("updated_at", sa.String(_TIME), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_conversations_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_conversations_goal_id_goals"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_conversations_task_id_tasks"),
        sa.CheckConstraint("revision >= 0", name="ck_conversations_revision"),
    )
    op.create_index("ix_conversations_owner_goal", "conversations", ["owner_id", "goal_id"])
    op.create_index("ix_conversations_owner_task", "conversations", ["owner_id", "task_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("conversation_id", sa.String(_ID), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("job_id", sa.String(_ID), nullable=True),
        sa.Column("client_request_key", sa.String(200), nullable=True),
        sa.Column("created_at", sa.String(_TIME), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_messages_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_messages_conversation_id_conversations"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_messages_job_id_jobs"),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_messages_conversation_id"),
        sa.CheckConstraint("sequence >= 1", name="ck_messages_sequence"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_owner_conversation", "messages", ["owner_id", "conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_owner_conversation", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_owner_task", table_name="conversations")
    op.drop_index("ix_conversations_owner_goal", table_name="conversations")
    op.drop_table("conversations")
