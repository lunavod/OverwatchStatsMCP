"""add joined_at to player_stats

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-03-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add joined_at column to player_stats (seconds from match start, default 0)."""
    op.add_column('player_stats', sa.Column('joined_at', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Remove joined_at column."""
    op.drop_column('player_stats', 'joined_at')
