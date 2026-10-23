"""Test doubles for the network edge only: a recording HTTP session and a real key."""

from __future__ import annotations

import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode('ascii')


def key_mapping(key, **overrides) -> dict:
    data = {
        'type': 'service_account',
        'client_email': 'notes-writer@example-project.iam.gserviceaccount.com',
        'private_key': pem(key),
        'private_key_id': 'abc123',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }
    data.update(overrides)
    return data


class FakeResponse(object):
    def __init__(self, status_code: int = 200, body=None, text: str = '') -> None:
        self.status_code = status_code
        self._body = body
        self.text = text if text else (json.dumps(body) if body is not None else '')

    def json(self):
        if self._body is None:
            raise ValueError('not json')
        return self._body


class FakeSession(object):
    """Answers POST / PATCH from queues and records every call."""

    def __init__(self, *, post=(), patch=()) -> None:
        self.post_answers = list(post)
        self.patch_answers = list(patch)
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        return self._next(self.post_answers)

    def patch(self, url, **kwargs):
        self.calls.append(('PATCH', url, kwargs))
        return self._next(self.patch_answers)

    @staticmethod
    def _next(answers):
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
