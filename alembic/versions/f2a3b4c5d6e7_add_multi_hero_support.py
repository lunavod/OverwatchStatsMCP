"""add multi-hero support with started_at and remove unique constraint

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-03-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add started_at JSON column and drop unique constraint on player_stat_id."""
    op.add_column('hero_stats', sa.Column('started_at', sa.JSON(), nullable=True, server_default='[]'))
    op.drop_constraint('hero_stats_player_stat_id_key', 'hero_stats', type_='unique')


def downgrade() -> None:
    """Remove started_at and restore unique constraint (after deduplicating)."""
    # Remove duplicates: keep only the first hero_stat per player_stat_id
    conn = op.get_bind()
    conn.execute(sa.text("""
        DELETE FROM hero_stats
        WHERE id NOT IN (
            SELECT DISTINCT ON (player_stat_id) id
            FROM hero_stats
            ORDER BY player_stat_id, id
        )
    """))
    op.create_unique_constraint('hero_stats_player_stat_id_key', 'hero_stats', ['player_stat_id'])
    op.drop_column('hero_stats', 'started_at')
