class LayouterError(Exception):
    """An actionable error that can be shown without a traceback."""


class ConfigError(LayouterError):
    pass


class BackendError(LayouterError):
    pass


class AmbiguousState(BackendError):
    pass
