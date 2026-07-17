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
