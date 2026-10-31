"""The operator's switch for keeping kato running with the lid closed.

Read fresh on every tick, so saving it in Settings applies within seconds and
no restart is needed. Off unless the operator turns it on: it holds a laptop
awake, and that is their decision to make.
"""

from __future__ import annotations

from utils_core_lib.utils_core_lib.text_utils import bool_from_text

from kato_core_lib.helpers.kato_settings_store_utils import live_setting

LID_AWAKE_ENABLED_KEY = 'KATO_LID_AWAKE_ENABLED'


def lid_awake_enabled() -> bool:
    return bool_from_text(live_setting(LID_AWAKE_ENABLED_KEY), default=False)
