from __future__ import annotations

from omegaconf import DictConfig

from kato_core_lib.kato_core_lib import KatoCoreLib


class KatoInstance(object):
    _app_instance: KatoCoreLib | None = None

    @staticmethod
    def init(core_lib_cfg: DictConfig) -> None:
        if KatoInstance._app_instance is None:
            KatoInstance._app_instance = KatoCoreLib(core_lib_cfg)

    @staticmethod
    def get() -> KatoCoreLib:
        if KatoInstance._app_instance is None:
            raise RuntimeError('KatoCoreLib is not initialized')
        return KatoInstance._app_instance
