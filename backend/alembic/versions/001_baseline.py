"""Baseline revision — schema owned by init_db() + _safe_alter until cutover to Alembic-only."""

from alembic import op

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Schema is created by app.database.init_db() on startup.
    # This revision marks the Alembic baseline aligned with schema_migrations.
    pass


def downgrade() -> None:
    pass
