"""create agent tables"""

from alembic import op
import sqlalchemy as sa


revision = '20260312_000001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'task',
        sa.Column('id', sa.VARCHAR(length=255), primary_key=True),
        sa.Column('summary', sa.VARCHAR(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('branch_name', sa.VARCHAR(length=255), nullable=False),
    )
    op.create_table(
        'review_comment',
        sa.Column('pull_request_id', sa.VARCHAR(length=255), primary_key=True),
        sa.Column('comment_id', sa.VARCHAR(length=255), primary_key=True),
        sa.Column('author', sa.VARCHAR(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('review_comment')
    op.drop_table('task')
