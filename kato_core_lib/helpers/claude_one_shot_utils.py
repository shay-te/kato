"""Backward-compatible re-export. Logic now lives in claude_core_lib."""
from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (  # noqa: F401
    ClaudeOneShotError,
    claude_one_shot,
    make_claude_one_shot,
)
