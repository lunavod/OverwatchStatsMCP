"""add rank_min, rank_max, is_wide_match to matches

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-03-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add rank_min, rank_max, and is_wide_match columns to matches."""
    op.add_column('matches', sa.Column('rank_min', sa.String(), nullable=True))
    op.add_column('matches', sa.Column('rank_max', sa.String(), nullable=True))
    op.add_column('matches', sa.Column('is_wide_match', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Remove rank_min, rank_max, and is_wide_match columns."""
    op.drop_column('matches', 'is_wide_match')
    op.drop_column('matches', 'rank_max')
    op.drop_column('matches', 'rank_min')
