"""The one error this lib raises: a sentence a person can act on."""


class SlackError(RuntimeError):
    """Slack could not be reached or refused the message; ``str()`` says why and what to do."""
