"""add swap_snapshots to player_stats

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-03-21 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, Sequence[str], None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add swap_snapshots JSON column to player_stats for per-swap cumulative stats."""
    op.add_column('player_stats', sa.Column('swap_snapshots', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove swap_snapshots column."""
    op.drop_column('player_stats', 'swap_snapshots')
