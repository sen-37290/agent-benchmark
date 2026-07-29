class AgentBenchError(Exception):
    """Base exception for expected, user-facing engine failures."""


class ConfigurationError(AgentBenchError):
    """A request or profile is invalid."""


class StageError(AgentBenchError):
    """A pipeline stage failed."""


class IntegrityError(AgentBenchError):
    """Transferred artifacts failed integrity verification."""
