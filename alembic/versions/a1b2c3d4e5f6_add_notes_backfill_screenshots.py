"""add notes, is_backfill, screenshots

Revision ID: a1b2c3d4e5f6
Revises: e34ff37c1c19
Create Date: 2026-03-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e34ff37c1c19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('matches', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('matches', sa.Column('is_backfill', sa.Boolean(), server_default='false', nullable=False))
    op.create_table('screenshots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.Uuid(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('screenshots')
    op.drop_column('matches', 'is_backfill')
    op.drop_column('matches', 'notes')
