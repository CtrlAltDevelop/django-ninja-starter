"""Every refusal this app makes, and the HTTP status each one carries.

One hierarchy, in a module of its own, because three transports and the mission
engine all raise from it and none of them should have to import a service to do
so. The status lives on the exception rather than in a mapping at each door: a
door that decides which code a refusal deserves is a door that will eventually
disagree with the other two.
"""


class ClubError(Exception):
    """Something a caller asked for that this app will not do."""

    status = 400


class ClubNotFound(ClubError, LookupError):
    """No such club, level, mission, or membership belonging to this caller.

    Deliberately the same answer whether a thing does not exist or belongs to
    somebody else: saying which would confirm the existence of other people's
    rows to anybody who can guess an id.
    """

    status = 404


class AlreadyAMember(ClubError):
    """This account is in a club already, and an account belongs to one at a time."""

    status = 409


class NotAMember(ClubError):
    """This account is in no club, so there is nothing to read or leave."""

    status = 409


class ClubClosed(ClubError):
    """The club is not taking members, or is not running at all."""

    status = 409


class InvalidLevels(ClubError):
    """A ladder that is not a ladder: a gap, a duplicate, or a rung out of order."""

    status = 400


class MissionRefused(ClubError):
    """A mission that cannot be defined, or an event it cannot be told about."""

    status = 400


class UnknownEvent(MissionRefused):
    """An event key nothing has registered, so no mission could ever match it."""

    status = 400
