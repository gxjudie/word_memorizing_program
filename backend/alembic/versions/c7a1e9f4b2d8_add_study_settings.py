"""add study settings

Adds the Learner's Study_Settings (Req 11.1, 11.2) as two NOT NULL integer
columns on ``users``: ``new_word_limit`` (default 50) and
``review_word_limit`` (default 100). Existing rows get the defaults from the
server defaults, so no separate backfill is needed. No CHECK constraint is
added; the 0..1000 range is enforced at the API boundary.

Batch mode is used so the migration runs on SQLite.

Revision ID: c7a1e9f4b2d8
Revises: d2f7e806032a
Create Date: 2026-11-22 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1e9f4b2d8'
down_revision: Union[str, Sequence[str], None] = 'd2f7e806032a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('new_word_limit', sa.Integer(), nullable=False,
                                      server_default=sa.text('50')))
        batch_op.add_column(sa.Column('review_word_limit', sa.Integer(), nullable=False,
                                      server_default=sa.text('100')))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('review_word_limit')
        batch_op.drop_column('new_word_limit')
