"""HTTP routes for the computer's own folder dialog (``native_folder_dialog``).

    GET    /api/fs/choose-folder     {available}: can a folder dialog open here?
    POST   /api/fs/choose-folder     {start, prompt} → {path} | {cancelled: true}
                                     (409 one is already open, 501 none can open here)

The POST waits while the dialog is on screen — the operator is choosing — and
answers with the folder they picked. Kato's own page is the only caller (the
CSRF guard covers it like every other route).
"""

from __future__ import annotations

from flask import Flask, jsonify, request

from kato_core_lib.helpers.native_folder_dialog import (
    FolderDialogBusy,
    FolderDialogUnavailable,
    NativeFolderDialog,
)

FOLDER_DIALOG_KEY = 'NATIVE_FOLDER_DIALOG'


def register_folder_dialog_routes(app: Flask, *, dialog: NativeFolderDialog | None = None) -> None:
    app.config[FOLDER_DIALOG_KEY] = dialog or NativeFolderDialog()

    @app.get('/api/fs/choose-folder')
    def folder_dialog_available():
        return jsonify({'available': app.config[FOLDER_DIALOG_KEY].available()})

    @app.post('/api/fs/choose-folder')
    def choose_folder():
        body = request.get_json(silent=True) or {}
        try:
            picked = app.config[FOLDER_DIALOG_KEY].choose(
                start=str(body.get('start') or ''), prompt=str(body.get('prompt') or ''),
            )
        except FolderDialogBusy as exc:
            return jsonify({'error': str(exc)}), 409
        except FolderDialogUnavailable as exc:
            return jsonify({'error': str(exc)}), 501
        if picked is None:
            return jsonify({'cancelled': True})
        return jsonify({'path': picked})


def folder_dialog_available(app: Flask) -> bool:
    dialog = app.config.get(FOLDER_DIALOG_KEY)
    return bool(dialog is not None and dialog.available())
