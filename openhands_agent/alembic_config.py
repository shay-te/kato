from pathlib import Path

from alembic.config import Config
from core_lib.data_layers.data.data_helpers import build_url
from omegaconf import DictConfig


def _to_mapping(data) -> dict:
    if isinstance(data, dict):
        return dict(data)
    if hasattr(data, 'items'):
        return dict(data.items())
    return {
        key: getattr(data, key)
        for key in dir(data)
        if not key.startswith('_') and not callable(getattr(data, key))
    }


def _build_sqlalchemy_url(cfg: DictConfig) -> str:
    return build_url(**_to_mapping(cfg.core_lib.data.sqlalchemy.url))


def build_alembic_config(cfg: DictConfig) -> Config:
    alembic_cfg = Config()
    alembic_settings = cfg.core_lib.alembic
    package_dir = Path(__file__).resolve().parent
    script_location = Path(alembic_settings.script_location)
    if not script_location.is_absolute():
        script_location = package_dir / script_location

    alembic_cfg.set_main_option('script_location', str(script_location))
    alembic_cfg.set_main_option('sqlalchemy.url', _build_sqlalchemy_url(cfg))
    alembic_cfg.set_main_option('version_table', alembic_settings.version_table)
    alembic_cfg.set_main_option(
        'render_as_batch',
        str(bool(alembic_settings.render_as_batch)).lower(),
    )
    return alembic_cfg
