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


def run_with_retry(operation, max_retries: int):
    last_response = None
    last_attempt = max_retries - 1

    for attempt in range(max_retries):
        try:
            response = operation()
        except Exception as exc:
            if attempt == last_attempt or not is_retryable_exception(exc):
                raise
            continue

        last_response = response
        if attempt < last_attempt and is_retryable_response(response):
            continue
        return response

    return last_response
