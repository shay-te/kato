"""What can go wrong asking Docker."""


class DockerUnavailable(RuntimeError):
    """Docker cannot be asked: not installed, or its engine is not running."""
