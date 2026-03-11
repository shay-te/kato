TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TRANSIENT_EXCEPTION_NAMES = {
    'ConnectionError',
    'ConnectTimeout',
    'ReadTimeout',
    'Timeout',
    'TimeoutError',
}


def is_retryable_exception(exc: Exception) -> bool:
    return exc.__class__.__name__ in TRANSIENT_EXCEPTION_NAMES or isinstance(
        exc,
        (ConnectionError, TimeoutError),
    )


def is_retryable_response(response: object) -> bool:
    return getattr(response, 'status_code', None) in TRANSIENT_STATUS_CODES
