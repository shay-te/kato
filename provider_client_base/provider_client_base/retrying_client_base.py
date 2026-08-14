from core_lib.client.client_base import ClientBase

from provider_client_base.provider_client_base.helpers.logging_utils import configure_logger
from provider_client_base.provider_client_base.helpers.retry_utils import run_with_retry


class RetryingClientBase(ClientBase):
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int,
        max_retries: int = 3,
    ) -> None:
        super().__init__(base_url.rstrip('/'))
        self.logger = configure_logger(self.__class__.__name__)
        self.set_headers({'Authorization': f'Bearer {token}'})
        self.set_timeout(timeout)
        self.max_retries = max(1, max_retries)

    def _abs_url(self, path: str) -> str:
        # Some providers (Bitbucket's pagination ``next`` field) hand back
        # an already-fully-qualified URL for the next page, not a relative
        # path. Blindly prepending ``base_url`` to that produced a doubled,
        # 404-ing URL like
        # ``https://api.bitbucket.org/2.0/https://api.bitbucket.org/2.0/...``
        # — every PR with more than one page of comments silently lost all
        # comments past page 1 on every scan cycle.
        if path.startswith(('http://', 'https://')):
            return path
        return f'{self.base_url.rstrip("/")}/{path.lstrip("/")}'

    # EVERY verb builds its URL through ``_abs_url`` (not ``ClientBase.__build_url``,
    # which has no "already absolute" check). ``ClientBase`` only wired that
    # check into ``_patch`` before, so GET/POST/PUT/DELETE — the verbs that
    # actually paginate — would double an absolute ``next`` URL and 404; only a
    # provider-side prefix-strip (Bitbucket's ``_relative_next_page_path``) hid
    # it, and its fallback branch still doubled. Overriding all verbs here is
    # the single guard, so callers can hand any verb an absolute URL safely.
    def _send(self, verb_callable, path: str, *args, **kwargs):
        return verb_callable(
            self._abs_url(path), *args, **self.process_kwargs(**kwargs),
        )

    def _get(self, path: str, *args, **kwargs):
        return self._send(self.session.get, path, *args, **kwargs)

    def _post(self, path: str, *args, **kwargs):
        return self._send(self.session.post, path, *args, **kwargs)

    def _put(self, path: str, *args, **kwargs):
        return self._send(self.session.put, path, *args, **kwargs)

    def _patch(self, path: str, *args, **kwargs):
        return self._send(self.session.patch, path, *args, **kwargs)

    def _delete(self, path: str, *args, **kwargs):
        return self._send(self.session.delete, path, *args, **kwargs)

    def _request_with_retry(self, method: str, verb_callable, path: str, **kwargs):
        return run_with_retry(
            lambda: verb_callable(path, **kwargs),
            self.max_retries,
            operation_name=self._retry_operation_name(method, path),
        )

    def _get_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('GET', self._get, path, **kwargs)

    def _post_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('POST', self._post, path, **kwargs)

    def _put_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('PUT', self._put, path, **kwargs)

    def _patch_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('PATCH', self._patch, path, **kwargs)

    def _delete_with_retry(self, path: str, **kwargs):
        return self._request_with_retry('DELETE', self._delete, path, **kwargs)

    def _retry_operation_name(self, method: str, path: str) -> str:
        return f'{self.__class__.__name__} {method} {self._abs_url(path)}'

    @staticmethod
    def raise_for_status_with_detail(response) -> None:
        """``raise_for_status`` that KEEPS the provider's explanation.

        Plain ``raise_for_status`` produces ``400 Client Error: Bad Request for
        url: ...`` and discards the response body — which is where every
        provider puts the one sentence that says what is actually wrong
        ("there are no changes to be pulled", "a pull request already exists",
        "branch not found"). The operator then sees a status code and has no
        way to act on it.

        Best-effort by design: if the body is empty, unparseable, or the
        response object doesn't behave like one, the original error is
        re-raised untouched. Surfacing detail must never turn a clean HTTP
        failure into a confusing one.
        """
        try:
            response.raise_for_status()
            return
        except Exception as exc:
            detail = RetryingClientBase._response_detail(response)
            if not detail:
                raise
            raise type(exc)(f'{exc}: {detail}') from exc

    @staticmethod
    def _response_detail(response) -> str:
        """The human-readable message out of an error response body."""
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            # Bitbucket nests it: {"error": {"message": "...", "fields": {...}}}.
            error = payload.get('error')
            if isinstance(error, dict):
                message = str(error.get('message', '') or '').strip()
                fields = error.get('fields')
                if message and isinstance(fields, dict) and fields:
                    return f'{message} ({fields})'
                if message:
                    return message
            # GitHub / GitLab keep it flat.
            for key in ('message', 'error_description', 'error'):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        try:
            text = str(response.text or '').strip()
        except Exception:
            return ''
        # A body long enough to be an HTML error page is noise, not detail.
        return text[:500] if 0 < len(text) <= 500 else ''
