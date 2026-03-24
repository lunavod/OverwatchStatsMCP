"""add has_attachments to matches

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-03-24 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = 'a9b0c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add has_attachments boolean column to matches."""
    op.add_column('matches', sa.Column('has_attachments', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    """Remove has_attachments column."""
    op.drop_column('matches', 'has_attachments')
