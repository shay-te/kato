from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core_lib.data_layers.data.db.sqlalchemy.base import Base
from openhands_agent.data_layers.data.review_comment import ReviewComment  # noqa: F401
from openhands_agent.data_layers.data.task import Task  # noqa: F401


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option('sqlalchemy.url'),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        version_table=config.get_main_option('version_table', 'alembic_version'),
        render_as_batch=config.get_main_option('render_as_batch', 'false') == 'true',
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table=config.get_main_option('version_table', 'alembic_version'),
            render_as_batch=config.get_main_option('render_as_batch', 'false') == 'true',
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
