"""Events every deployment has, because every deployment has accounts.

The floor of the registry: with no optional app installed at all, a club can
still be built out of signing up and signing in, which is enough for a
"welcome" mission and a streak.
"""

from apps.club.events import EventSpec, register

register(
    EventSpec(
        key="accounts.user.registered",
        label="An account was created",
        description="Sent once, the first time an account exists.",
        source="accounts",
    )
)

register(
    EventSpec(
        key="accounts.user.signed_in",
        label="An account signed in",
        description="Sent on every successful sign-in, whichever method was used.",
        fields={"method": "password, email_code, sms_code, magic_link, oauth"},
        source="accounts",
    )
)

register(
    EventSpec(
        key="club.member.joined",
        label="A member joined the club",
        description=(
            "Sent by this app itself when somebody joins, so 'welcome' missions "
            "need nothing else installed."
        ),
        source="club",
    )
)
