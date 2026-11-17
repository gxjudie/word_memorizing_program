"""google sign-in + gemini byok

Replaces the OpenAI "Sign in with ChatGPT" / plan-usage schema with the
Google sign-in + Gemini BYOK schema:

* drops ``oauth_credentials`` (OpenAI tokens only, no further use);
* creates ``gemini_credentials`` (one Fernet-encrypted key per Learner);
* creates ``revoked_app_sessions`` (logged-out App_Session ``sid`` values);
* on ``users``, drops ``oidc_client_id`` and narrows ``uq_user_oidc_subject``
  to ``(oidc_issuer, oidc_subject)``.

Batch mode is used for the ``users`` change so the migration runs on SQLite.

Revision ID: d2f7e806032a
Revises: fc993ac91694
Create Date: 2026-10-08 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f7e806032a'
down_revision: Union[str, Sequence[str], None] = 'fc993ac91694'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('oauth_credentials')

    op.create_table('gemini_credentials',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('api_key_encrypted', sa.Text(), nullable=False),
    sa.Column('key_hint', sa.String(length=4), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )

    op.create_table('revoked_app_sessions',
    sa.Column('sid', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('sid')
    )
    with op.batch_alter_table('revoked_app_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_revoked_app_sessions_expires_at'), ['expires_at'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_user_oidc_subject', type_='unique')
        batch_op.drop_column('oidc_client_id')
        batch_op.create_unique_constraint('uq_user_oidc_subject', ['oidc_issuer', 'oidc_subject'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_user_oidc_subject', type_='unique')
        batch_op.add_column(sa.Column('oidc_client_id', sa.String(length=255), server_default='', nullable=False))
        batch_op.create_unique_constraint('uq_user_oidc_subject', ['oidc_issuer', 'oidc_client_id', 'oidc_subject'])

    with op.batch_alter_table('revoked_app_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_revoked_app_sessions_expires_at'))
    op.drop_table('revoked_app_sessions')

    op.drop_table('gemini_credentials')

    op.create_table('oauth_credentials',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('access_token_encrypted', sa.Text(), nullable=False),
    sa.Column('refresh_token_encrypted', sa.Text(), nullable=False),
    sa.Column('access_token_expires_at', sa.DateTime(), nullable=False),
    sa.Column('granted_scopes', sa.JSON(), nullable=False),
    sa.Column('client_id', sa.String(length=255), nullable=False),
    sa.Column('ext_agent_host_id', sa.String(length=255), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
