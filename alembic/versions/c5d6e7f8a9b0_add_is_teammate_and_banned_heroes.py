"""add is_teammate to player_stats and banned_heroes to matches

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-03-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_teammate to player_stats and banned_heroes to matches."""
    op.add_column('player_stats', sa.Column('is_teammate', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('matches', sa.Column('banned_heroes', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove is_teammate and banned_heroes columns."""
    op.drop_column('player_stats', 'is_teammate')
    op.drop_column('matches', 'banned_heroes')
