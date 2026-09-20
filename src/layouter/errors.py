"""User-facing configuration and backend failures translated into CLI exit codes."""

class LayouterError(Exception):
    """An actionable error that can be shown without a traceback."""


class ConfigError(LayouterError):
    pass


class BackendError(LayouterError):
    pass


class WindowDiscoveryTimeout(BackendError):
    """A launch produced no new matching compositor window before the deadline."""


class AmbiguousState(BackendError):
    pass
