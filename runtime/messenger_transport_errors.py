class MessengerTransportError(RuntimeError):
    """Base error for outbound messenger provider transport failures."""


class MessengerMediaNotReadyError(MessengerTransportError):
    """Raised when a provider accepts media upload but cannot send it yet."""
