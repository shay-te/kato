"""``GET /api/lid-awake``: will closing the lid stop kato right now?"""

from __future__ import annotations

from flask import Flask, jsonify

from kato_core_lib.data_layers.service.lid_awake.status_text import status_payload


def register_lid_awake_routes(app: Flask) -> None:
    @app.get('/api/lid-awake')
    def get_lid_awake():
        service = app.config.get('LID_AWAKE_SERVICE')
        return jsonify(status_payload(service.status() if service else {}))
