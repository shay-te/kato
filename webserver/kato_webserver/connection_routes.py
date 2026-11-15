"""HTTP route for kato's one apply-and-recheck service (``connection_recheck``).

    POST   /api/connections/recheck   → {status: restarting | restart_needed | checked, ok, error, working}

Settings calls it after a successful "Test connection" and after a save: the
saved connection settings are put to work (a restart, when kato started with
others) or kato's own check runs again — whose outcome is the one connection
status the banner reads. 503 until kato's agent service exists.
"""

from __future__ import annotations

from flask import Flask, jsonify

CONNECTION_RECHECK_KEY = 'CONNECTION_RECHECK'


def register_connection_routes(app: Flask) -> None:
    @app.post('/api/connections/recheck')
    def recheck_connections():
        recheck = app.config.get(CONNECTION_RECHECK_KEY)
        if recheck is None:
            return jsonify({'error': 'kato is still starting — try again in a moment'}), 503
        return jsonify(recheck.recheck())
