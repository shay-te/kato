"""The one error this lib raises: a sentence a person can act on."""


class GoogleDocsError(RuntimeError):
    """Signing in or writing the document failed; ``str()`` says why and what to do."""
