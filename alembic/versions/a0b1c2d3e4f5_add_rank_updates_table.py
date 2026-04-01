"""add rank_updates table

Revision ID: a0b1c2d3e4f5
Revises: 6d4aa875920d
Create Date: 2026-04-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b1c2d3e4f5'
down_revision: Union[str, Sequence[str], None] = '6d4aa875920d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create rank_updates table."""
    op.create_table(
        'rank_updates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.Uuid(), nullable=False),
        sa.Column('rank', sa.String(), nullable=False),
        sa.Column('division', sa.Integer(), nullable=False),
        sa.Column('progress_pct', sa.Integer(), nullable=False),
        sa.Column('delta_pct', sa.Integer(), nullable=False),
        sa.Column('demotion_protection', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('modifiers', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('match_id'),
    )


def downgrade() -> None:
    """Drop rank_updates table."""
    op.drop_table('rank_updates')
