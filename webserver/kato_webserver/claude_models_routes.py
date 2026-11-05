"""HTTP route for the Claude models the settings model fields suggest.

    GET /api/claude/models   {models: [{id, label}]}

The aliases first (``sonnet`` — always the newest of the family), then every
pinned version this host knows the account has (``claude-sonnet-4-6``): an
older plan runs an older model, and typing a name the CLI would take was the
only way to pick one. Suggestions only — the fields stay free text, and what is
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
        {'id': model['id'], 'label': f"{model['label']} — always the newest {model['id'].capitalize()}"}
        for model in discover_models()
    ]
    return aliases + discover_pinned_models()


def register_claude_models_routes(app: Flask) -> None:

    @app.get('/api/claude/models')
    def list_claude_models():
        try:
            models = claude_model_suggestions()
        except Exception:
            app.logger.exception('could not list the Claude models')
            models = []
        return jsonify({'models': models})
