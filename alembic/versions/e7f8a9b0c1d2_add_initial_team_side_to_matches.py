"""add initial_team_side to matches

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-03-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add initial_team_side column to matches."""
    op.add_column('matches', sa.Column('initial_team_side', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove initial_team_side column."""
    op.drop_column('matches', 'initial_team_side')
