"""User-facing configuration and backend failures translated into CLI exit codes."""

class LayouterError(Exception):
    """An actionable error that can be shown without a traceback."""


class ConfigError(LayouterError):
    pass


class BackendError(LayouterError):
    pass


class AmbiguousState(BackendError):
    pass
