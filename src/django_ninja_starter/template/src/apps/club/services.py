"""Everything this app decides, once, for every transport.

The three doors -- REST, GraphQL, gRPC -- translate and nothing else. Every rule
that matters is here, because a rule enforced at a door is a rule the other two
doors do not have.

Two things are worth reading before the rest.

**The engine is the point.** :func:`track` is what makes missions automatic:
another app says "this happened to this account", and everything after that --
finding the missions listening, deciding which of them this satisfies, counting
progress, paying XP, moving a level -- happens here without the caller knowing
any of it exists. A call site that adds `track` gains every mission an operator
ever writes against that event, including the ones written next year.

**Paying twice is the failure to design against.** XP is money-shaped: it buys
levels, and levels buy whatever a deployment attaches to them. So every award
takes a `reference` unique per member and is written inside the membership's row
lock, which makes a replayed event, a retried request and two racing workers all
land on one row instead of three.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.club import signals
from apps.club.criteria import matches, validate
from apps.club.errors import (
    AlreadyAMember,
    ClubClosed,
    ClubError,
    ClubNotFound,
    InvalidLevels,
    MembershipSuspended,
    MissionRefused,
    NotAMember,
    UnknownEvent,
)
from apps.club.events import Occurrence, known
from apps.club.models import (
    Club,
    ClubLevel,
    ClubStatus,
    CountedOccurrence,
    Membership,
    MembershipStatus,
    Mission,
    MissionProgress,
    Repeat,
    XpAward,
)
from apps.club.standing import Standing, ladder, level_for, standing_of, xp_of

DEFAULT_PAGE = 50
MAX_PAGE = 200

__all__ = [
    "ClubService",
    "award_payload",
    "club_payload",
    "club_service",
    "level_payload",
    "membership_payload",
    "mission_payload",
    "page_size",
    "track",
]


def page_size(asked: int | None) -> int:
    """How many rows a listing returns, bounded so one request cannot ask for everything."""
    default = int(getattr(settings, "CLUB_PAGE_SIZE", DEFAULT_PAGE))
    ceiling = int(getattr(settings, "CLUB_MAX_PAGE_SIZE", MAX_PAGE))
    return max(1, min(int(asked or default), ceiling))


#: How long one run of a repeating mission lasts, by repeat mode. ``None`` means
#: the mission does not reset -- it either pays once ever, or every single time.
WINDOWS: dict[str, timedelta | None] = {
    str(Repeat.ONCE): None,
    str(Repeat.EVERY_TIME): None,
    str(Repeat.DAILY): timedelta(days=1),
    str(Repeat.WEEKLY): timedelta(weeks=1),
}

#: The event this app emits itself, when somebody joins. Registered in
#: :mod:`apps.club.bridges.accounts`.
JOINED = "club.member.joined"


class ClubService:
    """The whole of what a club can do. One instance, held at the bottom of this module."""

    # -- reading -----------------------------------------------------------

    def clubs(self, *, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        """Every club somebody could be in, newest ladder counts included."""
        page = page_size(limit)
        rows = Club.objects.exclude(status=str(ClubStatus.ARCHIVED)).order_by("name")
        return [club_payload(club) for club in rows[max(0, offset) : max(0, offset) + page]]

    def club(self, slug: str) -> dict[str, Any]:
        """One club and the ladder it defines."""
        found = Club.objects.filter(slug=slug).first()
        if found is None:
            raise ClubNotFound("No such club.")
        return club_payload(found, levels=ladder(found.pk))

    def levels(self, slug: str) -> list[dict[str, Any]]:
        return [level_payload(level) for level in ladder(self._club(slug).pk)]

    def membership_for(self, user: Any, *, required: bool = True) -> Membership | None:
        """This account's membership, or nothing if it is in no club."""
        found = (
            Membership.objects.select_related("club")
            .filter(user=user)
            .exclude(status=str(MembershipStatus.LEFT))
            .first()
        )
        if found is None and required:
            raise NotAMember("This account is not in a club.")
        return found

    def me(self, user: Any) -> dict[str, Any]:
        """Where this account stands: its club, its XP, its rung and the next one."""
        membership = self.membership_for(user)
        assert membership is not None
        return membership_payload(membership, standing_of(membership))

    def awards(
        self, user: Any, *, limit: int | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Every XP this account has been paid, newest first. The audit trail of a level."""
        membership = self.membership_for(user)
        assert membership is not None
        page = page_size(limit)
        rows = (
            XpAward.objects.filter(membership=membership, club_id=membership.club_id)
            .select_related("mission")
            .order_by("-created_at")
        )
        return [award_payload(award) for award in rows[max(0, offset) : max(0, offset) + page]]

    def missions(self, user: Any) -> list[dict[str, Any]]:
        """This account's club's missions, each with how far this member has got."""
        membership = self.membership_for(user)
        assert membership is not None
        now = timezone.now()
        rows = Mission.objects.filter(club_id=membership.club_id, is_enabled=True).order_by("title")
        progress = {
            record.mission_id: record
            for record in MissionProgress.objects.filter(membership=membership)
        }
        return [
            mission_payload(mission, progress.get(mission.pk), now=now)
            for mission in rows
            if _running(mission, now)
        ]

    def leaderboard(self, user: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
        """The club's members by XP, highest first.

        Scoped to the caller's own club rather than taking one as a parameter:
        a leaderboard is a thing you are in, and letting an account read any
        club's membership list is a directory of everybody's accounts.
        """
        membership = self.membership_for(user)
        assert membership is not None
        page = page_size(limit)
        rungs = ladder(membership.club_id)
        members = (
            Membership.objects.filter(
                club_id=membership.club_id, status=str(MembershipStatus.ACTIVE)
            )
            .select_related("user")
            .annotate(earned=Sum("awards__xp", filter=Q(awards__club_id=membership.club_id)))
        )
        ranked = []
        for member in members:
            xp = int(member.earned or 0)
            reached, _ = level_for(rungs, xp)
            ranked.append((xp, member, reached))
        ranked.sort(key=lambda row: (-row[0], row[1].joined_at))
        return [
            {
                "position": index + 1,
                "membership_id": member.pk,
                "username": getattr(member.user, "username", ""),
                "xp": xp,
                "level": level_payload(reached) if reached is not None else None,
                "is_you": member.pk == membership.pk,
            }
            for index, (xp, member, reached) in enumerate(ranked[:page])
        ]

    # -- joining and leaving ----------------------------------------------

    def join(self, user: Any, slug: str) -> dict[str, Any]:
        """Put this account in a club, if it is in none and the club is taking members.

        The one-to-one on `Membership.user` is what actually enforces "one club
        at a time": two requests naming two clubs both pass the check above and
        the database refuses the second, which is the only place that answer is
        reliable. Rejoining the club you left is allowed and keeps your XP --
        leaving is not a way to reset a ladder you are near the top of.
        """
        club = self._club(slug)
        if not club.accepts_members:
            raise ClubClosed(f"{club.name} is not taking members.")
        if not club.is_open:
            raise ClubClosed(f"{club.name} is joined by invitation, not by asking.")
        return self._enrol(user, club)

    def add_member(self, user: Any, slug: str, *, by: Any = None) -> dict[str, Any]:
        """Put somebody in a club from the back office, whatever its join policy says.

        An invite-only club still has to have a way in, and this is it. The
        club's *status* is still honoured -- an archived club takes nobody, by
        any route -- because that is a statement about the club rather than about
        who is allowed to decide.
        """
        club = self._club(slug)
        if not club.accepts_members:
            raise ClubClosed(f"{club.name} is not taking members.")
        return self._enrol(user, club, by=by)

    def _enrol(self, user: Any, club: Club, *, by: Any = None) -> dict[str, Any]:
        existing = Membership.objects.filter(user=user).first()
        if existing is not None and existing.status != str(MembershipStatus.LEFT):
            if existing.club_id == club.pk:
                return membership_payload(existing, standing_of(existing))
            raise AlreadyAMember(
                "This account is already in a club, and an account is in one at a time. "
                "Leave that one first."
            )
        try:
            with transaction.atomic():
                if existing is not None:
                    existing.club = club
                    existing.status = str(MembershipStatus.ACTIVE)
                    existing.left_at = None
                    existing.save(update_fields=["club", "status", "left_at", "updated_at"])
                    membership = existing
                else:
                    membership = Membership.objects.create(user=user, club=club)
                signals.announce(
                    signals.member_joined,
                    membership=membership_payload(membership, standing_of(membership)),
                )
        except IntegrityError:
            # Lost the race to another request for this same account.
            raise AlreadyAMember("This account is already in a club.") from None
        if known(JOINED):
            # This app's own event, tracked like anybody else's, so a welcome
            # mission is an ordinary mission. Keyed on the club: leaving and
            # coming back is not a second welcome.
            self.track(Occurrence(key=JOINED, user=user, reference=f"joined:{club.pk}"))
        return membership_payload(membership, standing_of(membership))

    def leave(self, user: Any) -> dict[str, Any]:
        """Take this account out of its club, keeping its history.

        The row stays, marked ``left``, so the XP and the awards survive. A club
        that wanted leaving to be a reset would be a club where the way to escape
        a mistake is to leave and come back, and that is a rule people learn.
        """
        membership = self.membership_for(user)
        assert membership is not None
        if membership.status == str(MembershipStatus.SUSPENDED):
            raise MembershipSuspended(
                "This membership is suspended. An operator lifts a suspension; leaving does not."
            )
        with transaction.atomic():
            membership.status = str(MembershipStatus.LEFT)
            membership.left_at = timezone.now()
            membership.save(update_fields=["status", "left_at", "updated_at"])
            signals.announce(
                signals.member_left,
                membership=membership_payload(membership, standing_of(membership)),
            )
        return membership_payload(membership, standing_of(membership))

    # -- the ladder --------------------------------------------------------

    def set_levels(self, slug: str, rungs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace a club's ladder with this one, refusing anything that is not a ladder.

        Whole-ladder rather than one rung at a time, because the rules -- numbered
        from one, no gaps, thresholds strictly increasing -- are about the set and
        cannot be checked against a single row. Saving them one at a time is what
        the admin does, and :meth:`ClubLevel.clean` guards that path separately.
        """
        club = self._club(slug)
        prepared = _validated_ladder(rungs)
        with transaction.atomic():
            ClubLevel.objects.filter(club=club).delete()
            created = [
                ClubLevel.objects.create(
                    club=club,
                    position=rung["position"],
                    name=rung["name"],
                    xp_required=rung["xp_required"],
                    logo=rung.get("logo", ""),
                    perks=rung.get("perks", ""),
                    metadata=rung.get("metadata", {}),
                )
                for rung in prepared
            ]
        return [level_payload(level) for level in created]

    def check_ladder(self, slug: str) -> None:
        """Refuse a club whose ladder has drifted out of shape. What `check` calls."""
        _validated_ladder(
            [
                {
                    "position": level.position,
                    "name": level.name,
                    "xp_required": level.xp_required,
                }
                for level in ladder(self._club(slug).pk)
            ]
        )

    # -- missions ----------------------------------------------------------

    def define_mission(
        self,
        slug: str,
        *,
        code: str,
        title: str,
        event: str,
        xp: int,
        description: str = "",
        criteria: dict[str, Any] | None = None,
        repeat: str = str(Repeat.ONCE),
        target_count: int = 1,
        is_enabled: bool = False,
        starts_at: Any = None,
        ends_at: Any = None,
    ) -> dict[str, Any]:
        """Write or update one mission, refusing one that could never fire.

        An event nothing has registered is the refusal that matters: a mission
        listening for ``shop.order.payed`` is not a stricter mission, it is a
        dead one, and it is dead silently -- which an operator discovers weeks
        later when nobody has earned anything.
        """
        club = self._club(slug)
        if not known(event):
            raise UnknownEvent(
                f"Nothing registers the event {event!r}, so no mission could ever "
                "match it. See GET /club/events for what this deployment emits."
            )
        if xp < 1:
            raise MissionRefused("A mission has to pay at least 1 XP.")
        if target_count < 1:
            raise MissionRefused("A mission has to need at least one event.")
        if repeat not in Repeat.values:
            raise MissionRefused(f"{repeat!r} is not a repeat mode.")
        if not code.strip():
            raise MissionRefused("A mission needs a code; it is how it is found again.")
        if starts_at and ends_at and ends_at <= starts_at:
            raise MissionRefused("A mission cannot end before it starts.")
        checked = validate(criteria)
        mission, _ = Mission.objects.update_or_create(
            club=club,
            code=code,
            defaults={
                "title": title,
                "description": description,
                "event": event,
                "criteria": checked,
                "xp": xp,
                "repeat": repeat,
                "target_count": target_count,
                "is_enabled": is_enabled,
                "starts_at": starts_at,
                "ends_at": ends_at,
            },
        )
        return mission_payload(mission, None, now=timezone.now())

    # -- the engine --------------------------------------------------------

    def track(self, occurrence: Occurrence) -> list[dict[str, Any]]:
        """Tell the app something happened, and let the missions decide what it was worth.

        Silent about almost everything, on purpose. An account in no club, an
        event no mission listens for, a mission that is not running yet -- none of
        those are errors, because the caller is a shop or a wallet that should
        not have to know whether the club app is configured. It returns the
        awards it paid, which is usually an empty list.

        Unknown event keys *are* refused, because that is a programming mistake
        at a call site rather than a state of the deployment, and swallowing it
        would make a typo undetectable.
        """
        if not known(occurrence.key):
            raise UnknownEvent(
                f"{occurrence.key!r} is not a registered event. Register it with "
                "apps.club.events.register before tracking it."
            )
        membership = self.membership_for(occurrence.user, required=False)
        if membership is None or not membership.is_active:
            return []
        now = timezone.now()
        candidates = [
            mission
            for mission in Mission.objects.filter(
                club_id=membership.club_id, event=occurrence.key, is_enabled=True
            )
            if _running(mission, now)
            and matches(mission.criteria, value=occurrence.value, metadata=occurrence.metadata)
        ]
        paid: list[dict[str, Any]] = []
        for mission in candidates:
            award = self._advance(membership, mission, occurrence, now=now)
            if award is not None:
                paid.append(award)
        return paid

    def _advance(
        self, membership: Membership, mission: Mission, occurrence: Occurrence, *, now: Any
    ) -> dict[str, Any] | None:
        """Count one matching event against one mission, paying if it finishes it.

        Everything here happens inside the membership's row lock, which is what
        makes two events arriving together count as two rather than racing to
        write the same progress row.
        """
        with transaction.atomic():
            locked = Membership.objects.select_for_update().get(pk=membership.pk)
            if not locked.is_active or locked.club_id != mission.club_id:
                # Left, suspended or moved club while this event was on its way.
                return None
            progress, _ = MissionProgress.objects.get_or_create(membership=locked, mission=mission)
            if not _may_run_again(mission, progress, now=now):
                return None
            if occurrence.reference:
                _, fresh = CountedOccurrence.objects.get_or_create(
                    progress=progress, reference=occurrence.reference
                )
                if not fresh:
                    # The same event delivered again: it has already counted.
                    return None
            progress.count += 1
            if progress.count < mission.target_count:
                progress.save(update_fields=["count", "updated_at"])
                return None
            progress.count = 0
            progress.completions += 1
            progress.last_completed_at = now
            progress.save(update_fields=["count", "completions", "last_completed_at", "updated_at"])
            reference = _award_reference(mission, progress.completions, occurrence)
            award = self._pay(
                locked,
                xp=mission.xp,
                reference=reference,
                reason=mission.title,
                mission=mission,
                metadata={"event": occurrence.key, "value": occurrence.value},
            )
            if award is not None:
                signals.announce(
                    signals.mission_completed,
                    membership=membership_payload(locked, standing_of(locked)),
                    mission=mission_payload(mission, progress, now=now),
                    award=award,
                )
            return award

    def _pay(
        self,
        membership: Membership,
        *,
        xp: int,
        reference: str,
        reason: str,
        mission: Mission | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Write one award, once. Returns nothing if this reference already paid.

        The caller is expected to hold the membership's lock. The level is read
        before and after so a rung crossed by this award is announced -- which is
        the event anybody building a "congratulations" email is waiting for.
        """
        if XpAward.objects.filter(membership=membership, reference=reference).exists():
            return None
        rungs = ladder(membership.club_id)
        before, _ = level_for(rungs, xp_of(membership))
        try:
            award = XpAward.objects.create(
                membership=membership,
                club_id=membership.club_id,
                mission=mission,
                xp=xp,
                reason=reason,
                reference=reference,
                metadata=metadata or {},
            )
        except IntegrityError:
            return None
        after, _ = level_for(rungs, xp_of(membership))
        payload = award_payload(award)
        signals.announce(
            signals.xp_awarded,
            membership=membership_payload(membership, standing_of(membership, levels=rungs)),
            award=payload,
        )
        if getattr(before, "pk", None) != getattr(after, "pk", None):
            signals.announce(
                signals.level_changed,
                membership=membership_payload(membership, standing_of(membership, levels=rungs)),
                previous=level_payload(before) if before is not None else None,
                current=level_payload(after) if after is not None else None,
            )
        return payload

    def grant(
        self, user: Any, *, xp: int, reason: str, reference: str, by: Any = None
    ) -> dict[str, Any]:
        """Pay XP by hand, from the back office.

        The counterpart of an adjustment in the wallet: no event, no mission, an
        operator's decision. A reason is required, because an unexplained level
        is the one a member will certainly ask about and the operator will not
        remember.
        """
        if xp < 1:
            raise ClubError("A grant has to be at least 1 XP.")
        if not reason.strip():
            raise ClubError("A grant needs a reason; it is the only record of why.")
        if not reference.strip():
            raise ClubError("A reference is required; it is what makes a retry safe.")
        membership = self.membership_for(user)
        assert membership is not None
        metadata: dict[str, Any] = {"granted": True}
        if getattr(by, "pk", None):
            metadata["granted_by"] = str(by.pk)
        with transaction.atomic():
            locked = Membership.objects.select_for_update().get(pk=membership.pk)
            paid = self._pay(locked, xp=xp, reference=reference, reason=reason, metadata=metadata)
            if paid is None:
                existing = XpAward.objects.get(membership=locked, reference=reference)
                return award_payload(existing)
            return paid

    # -- helpers -----------------------------------------------------------

    def _club(self, slug: str) -> Club:
        found = Club.objects.filter(slug=slug).first()
        if found is None:
            raise ClubNotFound("No such club.")
        return found

    def membership_by_id(self, membership_id: UUID) -> Membership:
        found = Membership.objects.select_related("club", "user").filter(pk=membership_id).first()
        if found is None:
            raise ClubNotFound("No such membership.")
        return found


def _running(mission: Mission, now: Any) -> bool:
    """Whether a mission's window is open. A mission outside it is simply not there."""
    if mission.starts_at and now < mission.starts_at:
        return False
    return not (mission.ends_at and now >= mission.ends_at)


def _may_run_again(mission: Mission, progress: MissionProgress, *, now: Any) -> bool:
    """Whether this member may earn this mission again right now."""
    if mission.repeat == str(Repeat.ONCE):
        return progress.completions == 0
    window = WINDOWS[mission.repeat]
    if window is None:
        return True
    last = progress.last_completed_at
    return last is None or now - last >= window


def _award_reference(mission: Mission, run: int, occurrence: Occurrence) -> str:
    """The idempotency key an automatic award is written under.

    Built from the caller's own reference when it gave one, so the same event
    redelivered pays once however many times it arrives. Without one, the run
    number is the best this app can do -- which still stops a completion being
    written twice, but cannot tell two identical events apart from one retried.
    """
    if occurrence.reference:
        return f"mission:{mission.pk}:{occurrence.reference}"
    return f"mission:{mission.pk}:run:{run}"


def _validated_ladder(rungs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refuse anything that is not a ladder: a gap, a repeat, or a rung out of order."""
    if not rungs:
        return []
    ordered = sorted(rungs, key=lambda rung: int(rung["position"]))
    positions = [int(rung["position"]) for rung in ordered]
    if positions != list(range(1, len(positions) + 1)):
        raise InvalidLevels(
            "Levels are numbered from 1 upwards with no gaps. "
            f"These are {positions}, which leaves a member somewhere that does not exist."
        )
    thresholds = [int(rung["xp_required"]) for rung in ordered]
    if thresholds[0] != 0:
        raise InvalidLevels("The first level is where everybody starts, so it needs 0 XP.")
    for lower, higher in zip(thresholds, thresholds[1:], strict=False):
        if higher <= lower:
            raise InvalidLevels(
                f"Each level needs more XP than the one below it; {higher} does not "
                f"come after {lower}."
            )
    for rung in ordered:
        if not str(rung.get("name", "")).strip():
            raise InvalidLevels("Every level needs a name; it is what a member is shown.")
    return ordered


# -- payloads -------------------------------------------------------------


def club_payload(club: Club, *, levels: list[ClubLevel] | None = None) -> dict[str, Any]:
    """One club, in the shape every transport answers with."""
    payload: dict[str, Any] = {
        "id": club.pk,
        "name": club.name,
        "slug": club.slug,
        "description": club.description,
        "logo": club.logo,
        "status": club.status,
        "join_policy": club.join_policy,
        "is_open": club.is_open,
        "member_count": club.memberships.filter(status=str(MembershipStatus.ACTIVE)).count(),
        "metadata": club.metadata,
        "created_at": club.created_at.isoformat(),
    }
    if levels is not None:
        payload["levels"] = [level_payload(level) for level in levels]
    return payload


def level_payload(level: ClubLevel) -> dict[str, Any]:
    return {
        "id": level.pk,
        "position": level.position,
        "name": level.name,
        "xp_required": level.xp_required,
        "logo": level.logo,
        "perks": level.perks,
        "metadata": level.metadata,
    }


def membership_payload(membership: Membership, standing: Standing) -> dict[str, Any]:
    """A member and where they stand, which is the answer to almost every question here."""
    return {
        "id": membership.pk,
        "club": {
            "id": membership.club.pk,
            "name": membership.club.name,
            "slug": membership.club.slug,
            "logo": membership.club.logo,
        },
        "status": membership.status,
        "xp": standing.xp,
        "level": level_payload(standing.level) if standing.level is not None else None,
        "next_level": (
            level_payload(standing.next_level) if standing.next_level is not None else None
        ),
        "xp_to_next": standing.xp_to_next,
        "progress": round(standing.progress, 4),
        "joined_at": membership.joined_at.isoformat(),
    }


def mission_payload(
    mission: Mission, progress: MissionProgress | None, *, now: Any
) -> dict[str, Any]:
    """One mission as a member sees it: what it wants, and how far they are."""
    done = progress.completions if progress is not None else 0
    return {
        "id": mission.pk,
        "code": mission.code,
        "title": mission.title,
        "description": mission.description,
        "event": mission.event,
        "criteria": mission.criteria,
        "xp": mission.xp,
        "repeat": mission.repeat,
        "target_count": mission.target_count,
        "count": progress.count if progress is not None else 0,
        "completions": done,
        "completed": done > 0,
        "available": _may_run_again(mission, progress, now=now) if progress is not None else True,
        "last_completed_at": (
            progress.last_completed_at.isoformat()
            if progress is not None and progress.last_completed_at
            else None
        ),
        "starts_at": mission.starts_at.isoformat() if mission.starts_at else None,
        "ends_at": mission.ends_at.isoformat() if mission.ends_at else None,
    }


def award_payload(award: XpAward) -> dict[str, Any]:
    return {
        "id": award.pk,
        "xp": award.xp,
        "reason": award.reason,
        "reference": award.reference,
        "mission_id": award.mission_id,
        "metadata": award.metadata,
        "created_at": award.created_at.isoformat(),
    }


club_service = ClubService()


def track(
    user: Any,
    key: str,
    *,
    value: float = 1.0,
    metadata: dict[str, Any] | None = None,
    reference: str = "",
) -> list[dict[str, Any]]:
    """Tell the club app something happened. The whole integration surface.

    Safe to call from anywhere, including apps that have never heard of this one:
    it does nothing when the account is in no club and nothing when no mission
    cares. See :mod:`apps.club.events` for how an app declares what it emits.
    """
    return club_service.track(
        Occurrence(key=key, user=user, value=value, metadata=metadata or {}, reference=reference)
    )
