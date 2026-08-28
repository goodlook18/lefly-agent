"""Errors raised by the LeFly SDK."""


class DeviceClientError(RuntimeError):
    """Base error for client lifecycle and remote device failures."""


class RequestTimeoutError(DeviceClientError):
    """Raised when a command isn't acknowledged before its deadline."""


class ClientClosedError(DeviceClientError):
    """Raised when the client closes with unfinished requests."""


class DeviceDisconnectedError(DeviceClientError):
    """Raised when a connection drops before a command is acknowledged."""


class CommandOutcomeUnknownError(DeviceDisconnectedError):
    """Raised when transport loss makes a sent command's outcome unknowable."""

    outcome = "outcome_unknown"


class RemoteDeviceError(DeviceClientError):
    """A structured error returned by the remote device endpoint."""

    def __init__(self, code: str, message: str, recoverable: bool, details=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.details = details
