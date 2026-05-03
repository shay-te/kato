from hydra.core.config_search_path import ConfigSearchPath
from hydra.plugins.search_path_plugin import SearchPathPlugin


class KatoSearchPathPlugin(SearchPathPlugin):
    def manipulate_search_path(self, search_path: ConfigSearchPath) -> None:
        assert isinstance(search_path, ConfigSearchPath)
        search_path.append("kato_core_lib", "pkg://kato_core_lib.config")
        search_path.append("github_core_lib", "pkg://github_core_lib.config")
