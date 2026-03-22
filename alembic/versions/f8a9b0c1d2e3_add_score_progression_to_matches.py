"""add score_progression to matches

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-03-22 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add score_progression JSON column to matches."""
    op.add_column('matches', sa.Column('score_progression', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove score_progression column."""
    op.drop_column('matches', 'score_progression')
