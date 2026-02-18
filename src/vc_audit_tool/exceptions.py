"""Domain-specific exceptions for valuation workflows."""


class ValidationError(ValueError):
    """Raised when valuation input data is incomplete or invalid."""


class DataSourceError(RuntimeError):
    """Raised when a required external or mocked data lookup fails."""
