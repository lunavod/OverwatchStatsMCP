"""add player_notes table

Revision ID: b7e8f9a0c1d2
Revises: f4d39762725a
Create Date: 2026-03-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e8f9a0c1d2'
down_revision: Union[str, Sequence[str], None] = 'f4d39762725a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'player_notes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('player_name', sa.String(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('player_notes')
