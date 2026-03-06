"""add title field to player_stats

Revision ID: c8f9a1b2d3e4
Revises: b7e8f9a0c1d2
Create Date: 2026-03-06 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8f9a1b2d3e4'
down_revision: Union[str, Sequence[str], None] = 'b7e8f9a0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('player_stats', sa.Column('title', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('player_stats', 'title')
