"""HTTP route for the Claude models the settings model fields suggest.

    GET /api/claude/models   {models: [{id, label, group: newest | version}]}

The aliases first (``sonnet`` — always the newest of the family), then every
pinned version (``claude-sonnet-4-6``): every released model kato knows, plus
any newer one the account's sources name. An older plan runs an older model, and
typing a name the CLI would take was the only way to pick one. The settings
field is a dropdown of these with "Other" for anything else, and whatever is
typed is turned into an id the CLI takes when it runs (``model_ids``).
"""

from __future__ import annotations

from flask import Flask, jsonify


def claude_model_suggestions() -> list[dict]:
    from claude_core_lib.claude_core_lib.helpers.model_catalog import (
        discover_models,
        discover_pinned_models,
    )

    aliases = [
        {'id': model['id'], 'label': f"{model['label']} — always the newest {model['id'].capitalize()}",
         'group': 'newest'}
        for model in discover_models()
    ]
    return aliases + [{**model, 'group': 'version'} for model in discover_pinned_models()]


def register_claude_models_routes(app: Flask) -> None:

    @app.get('/api/claude/models')
    def list_claude_models():
        try:
            models = claude_model_suggestions()
        except Exception:
            app.logger.exception('could not list the Claude models')
            models = []
        return jsonify({'models': models})
