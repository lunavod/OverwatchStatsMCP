"""add users table and multi-user support

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-04-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("google_sub", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("max_stored_matches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("google_sub"),
        sa.UniqueConstraint("email"),
    )

    # Truncate existing data — should have been exported beforehand
    op.execute("TRUNCATE TABLE player_notes CASCADE")
    op.execute("TRUNCATE TABLE matches CASCADE")

    # Add user_id to matches
    op.add_column("matches", sa.Column("user_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        "fk_matches_user_id", "matches", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_matches_user_id", "matches", ["user_id"])

    # Add user_id to player_notes and change unique constraint
    op.drop_constraint("player_notes_player_name_key", "player_notes", type_="unique")
    op.add_column("player_notes", sa.Column("user_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        "fk_player_notes_user_id", "player_notes", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_player_notes_user_id", "player_notes", ["user_id"])
    op.create_unique_constraint(
        "uq_player_notes_user_player", "player_notes", ["user_id", "player_name"]
    )


def downgrade() -> None:
    # player_notes: remove user_id, restore old unique constraint
    op.drop_constraint("uq_player_notes_user_player", "player_notes", type_="unique")
    op.drop_index("ix_player_notes_user_id", table_name="player_notes")
    op.drop_constraint("fk_player_notes_user_id", "player_notes", type_="foreignkey")
    op.drop_column("player_notes", "user_id")
    op.create_unique_constraint("player_notes_player_name_key", "player_notes", ["player_name"])

    # matches: remove user_id
    op.drop_index("ix_matches_user_id", table_name="matches")
    op.drop_constraint("fk_matches_user_id", "matches", type_="foreignkey")
    op.drop_column("matches", "user_id")

    # Drop users table
    op.drop_table("users")
