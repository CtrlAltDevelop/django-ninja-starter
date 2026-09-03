"""Decisions every authentication method shares, independent of transport.

Masking lives here rather than beside the response schema because it is a
decision -- how much of an address a client may be shown -- and all three
transports have to make the same one. What differs between them is only the
shape the masked string is carried in.
"""


class MaskingService:
    """Show enough of a destination to recognise it, not enough to learn it."""

    def email(self, value: str) -> str:
        local, separator, domain = value.partition("@")
        if not separator:
            return "***"
        return f"{local[:1]}***@{domain}"

    def phone(self, value: str) -> str:
        return f"***{value[-4:]}" if len(value) > 4 else "***"

    def destination(self, channel: str, destination: str) -> str:
        if channel == "email":
            return self.email(destination)
        if channel == "sms":
            return self.phone(destination)
        return "***"


masking_service = MaskingService()


def mask_email(value: str) -> str:
    """Back-compatible alias for :meth:`MaskingService.email`."""
    return masking_service.email(value)


def mask_phone(value: str) -> str:
    """Back-compatible alias for :meth:`MaskingService.phone`."""
    return masking_service.phone(value)


def mask(channel: str, destination: str) -> str:
    """Back-compatible alias for :meth:`MaskingService.destination`."""
    return masking_service.destination(channel, destination)
