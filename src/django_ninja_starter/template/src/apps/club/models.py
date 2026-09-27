"""The tables: clubs, the ladder each one defines, who is in it, and what they earned.

Three decisions shape everything here.

**An account belongs to one club at a time**, and that is a database constraint
rather than a check in a service. `Membership.user` is a one-to-one, so two
requests racing to join two different clubs cannot both succeed: the second one
loses to the unique index, which is the only arbiter that is still right when
the app is running in four processes.

**The ladder is contiguous.** Levels are numbered from one with no gaps and their
thresholds only ever go up, so "the level for this much XP" is a lookup rather
than an interpretation. A ladder with a gap at three, or two rungs needing the
same XP, is not a stricter ladder -- it is an ambiguous one, and the ambiguity
surfaces as members sitting at a level the club never meant to exist. The rule is
enforced when levels are saved, in :class:`ClubLevel.clean` and again in the
service that reorders them.

**XP is not a column.** It is the sum of the awards a member has been given, for
the same reason the wallet has no balance column: a stored total is a second copy
of a fact the awards already hold, and the two disagree the first time a process
dies between writing an award and updating the total. Awards are immutable and
idempotent per `reference`, so the sum is replayable and a retried mission
completion cannot pay twice.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class ClubStatus(models.TextChoices):
    """Whether a club is running, and whether it is taking anybody new."""

    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed to new members"
    ARCHIVED = "archived", "Archived"


class JoinPolicy(models.TextChoices):
    """How somebody gets in."""

    OPEN = "open", "Anyone may join"
    INVITE = "invite", "An operator adds them"


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    LEFT = "left", "Left"


class Repeat(models.TextChoices):
    """How often one mission may pay out.

    ``ONCE`` is an achievement -- joined, first purchase, profile completed.
    ``EVERY_TIME`` is a standing offer, which is the one that needs a ceiling
    thought about: a mission paying XP on every event with nothing bounding it
    is a mission somebody will farm.
    """

    ONCE = "once", "Once per member"
    EVERY_TIME = "every_time", "Every time it happens"
    DAILY = "daily", "Once a day"
    WEEKLY = "weekly", "Once a week"


class Club(models.Model):
    """One club: a name, a look, and the ladder and missions defined against it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=120,
        unique=True,
        help_text="What a client names this club in a URL. Stable: changing it breaks links.",
    )
    description = models.TextField(blank=True)
    logo = models.URLField(
        blank=True, help_text="The club's own badge, shown beside a member's level badge."
    )
    status = models.CharField(
        max_length=20, choices=ClubStatus.choices, default=str(ClubStatus.ACTIVE)
    )
    join_policy = models.CharField(
        max_length=20, choices=JoinPolicy.choices, default=str(JoinPolicy.OPEN)
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "club"
        verbose_name_plural = "clubs"

    def __str__(self) -> str:
        return self.name

    @property
    def accepts_members(self) -> bool:
        return self.status == str(ClubStatus.ACTIVE)

    @property
    def is_open(self) -> bool:
        """Whether an account can put itself in, as opposed to being put in."""
        return self.accepts_members and self.join_policy == str(JoinPolicy.OPEN)


class ClubLevel(models.Model):
    """One rung: what it is called, what it looks like, and the XP that reaches it.

    ``position`` is the rung's number, from one, with no gaps. ``xp_required`` is
    the total lifetime XP that puts a member on it -- a threshold, not a cost, so
    reaching level four does not spend the XP that got you there.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="levels")
    position = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="The rung's number, from 1 upwards with no gaps.",
    )
    name = models.CharField(max_length=120, help_text="What this rung is called: Bronze, Gold.")
    xp_required = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Total lifetime XP that reaches this rung. The first rung is 0: "
            "everybody starts somewhere."
        ),
    )
    logo = models.URLField(blank=True, help_text="The badge for this rung.")
    perks = models.TextField(
        blank=True, help_text="What the member gets here, in words a member reads."
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("club", "position")
        verbose_name = "level"
        constraints = [
            models.UniqueConstraint(fields=("club", "position"), name="club_level_position_unique"),
            models.UniqueConstraint(
                fields=("club", "xp_required"), name="club_level_threshold_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.position}. {self.name}"

    def clean(self) -> None:
        """The two things a rung cannot be: the first one costing XP, or out of order.

        Checked here as well as in the service because the admin edits these one
        row at a time, and a ladder broken through a form is broken for every
        member reading their level.
        """
        if self.position == 1 and self.xp_required != 0:
            raise ValidationError(
                {"xp_required": "The first level is where everybody starts, so it needs 0 XP."}
            )
        if self.club_id is None or getattr(self, "_checked_as_ladder", False):
            # The admin's ladder formset checks the new set as a whole; comparing
            # one rung with the rows it is about to replace would refuse valid edits.
            return
        neighbours = ClubLevel.objects.filter(club_id=self.club_id).exclude(pk=self.pk)
        below = neighbours.filter(position__lt=self.position).order_by("-position").first()
        above = neighbours.filter(position__gt=self.position).order_by("position").first()
        if below is not None and self.xp_required <= below.xp_required:
            raise ValidationError(
                {
                    "xp_required": (
                        f"Level {self.position} has to need more XP than level "
                        f"{below.position}, which needs {below.xp_required}."
                    )
                }
            )
        if above is not None and self.xp_required >= above.xp_required:
            raise ValidationError(
                {
                    "xp_required": (
                        f"Level {self.position} has to need less XP than level "
                        f"{above.position}, which needs {above.xp_required}."
                    )
                }
            )


class Membership(models.Model):
    """One account's place in one club.

    ``user`` is a one-to-one, which is the whole of the "a user is in at most one
    club" rule: not a service check that two concurrent joins can both pass, but
    an index the database refuses the second write against.

    A member who leaves keeps the row -- status ``left`` -- so their XP and their
    history survive rejoining, and so a club can tell "never joined" from "joined
    and went".
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="club_membership"
    )
    club = models.ForeignKey(Club, on_delete=models.PROTECT, related_name="memberships")
    status = models.CharField(
        max_length=20, choices=MembershipStatus.choices, default=str(MembershipStatus.ACTIVE)
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-joined_at",)
        indexes = [models.Index(fields=("club", "status"))]

    def __str__(self) -> str:
        return f"{self.user} in {self.club}"

    @property
    def is_active(self) -> bool:
        return self.status == str(MembershipStatus.ACTIVE)


class Mission(models.Model):
    """Something a member can do that the app notices by itself, and what it pays.

    A mission is a *rule*, not a task list: it names an event key, optionally
    narrows which of those events count, and says how much XP completing it is
    worth. Nobody marks a mission done -- the engine does, when the event that
    satisfies it arrives.

    ``event`` is a registered key such as ``shop.order.paid``. What may be
    registered is open: the built-in apps declare their own, and a project's own
    app declares its own the same way, which is what lets this work for code this
    starter has never seen.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name="missions")
    code = models.SlugField(
        max_length=80, help_text="Stable name for this mission within its club."
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    event = models.CharField(
        max_length=120,
        help_text="The registered event key this mission listens for, e.g. shop.order.paid.",
    )
    criteria = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Which of those events count, as a plain object: "
            '{"min_value": 100, "equals": {"currency": "USD"}}. Empty means all of them.'
        ),
    )
    xp = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], help_text="What completing it pays, in XP."
    )
    repeat = models.CharField(max_length=20, choices=Repeat.choices, default=str(Repeat.ONCE))
    target_count = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="How many matching events complete it once. 'Buy three things' is 3.",
    )
    is_enabled = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("club", "title")
        constraints = [
            models.UniqueConstraint(fields=("club", "code"), name="club_mission_code_unique")
        ]
        indexes = [models.Index(fields=("event", "is_enabled"))]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "A mission cannot end before it starts."})


class MissionProgress(models.Model):
    """How far one member has got with one mission, and when they last finished it.

    Kept per member and mission rather than recomputed from events, because the
    events belong to other apps: this app is told what happened, it does not own
    the history to re-read.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="mission_progress"
    )
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="progress")
    count = models.PositiveIntegerField(
        default=0, help_text="Matching events seen since the current run began."
    )
    completions = models.PositiveIntegerField(
        default=0, help_text="How many times this member has finished it."
    )
    last_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("mission__title",)
        verbose_name_plural = "mission progress"
        constraints = [
            models.UniqueConstraint(fields=("membership", "mission"), name="club_progress_unique")
        ]

    def __str__(self) -> str:
        return f"{self.membership} / {self.mission}"


class XpAward(models.Model):
    """One payment of XP, kept forever. The sum of these is a member's XP.

    Immutable and idempotent: ``reference`` is unique per membership, so a
    mission completion replayed by a retried request, a redelivered webhook or a
    job run twice pays once. ``mission`` is null for XP an operator granted by
    hand, which is the other way a member can be paid.

    ``club`` is where it was earned. A membership can leave one club and join
    another, and XP counted against the second club's ladder has to be XP that
    club paid -- otherwise joining a new club is a way to arrive at its top rung.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="awards")
    club = models.ForeignKey(
        Club,
        on_delete=models.PROTECT,
        related_name="awards",
        help_text=(
            "The club this was earned in. A member who moves clubs does not carry "
            "one ladder's XP up another."
        ),
    )
    mission = models.ForeignKey(
        Mission, on_delete=models.SET_NULL, null=True, blank=True, related_name="awards"
    )
    xp = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    reason = models.CharField(max_length=255, blank=True)
    reference = models.CharField(
        max_length=200,
        help_text="Idempotency key, unique per member. A repeat pays nothing twice.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "reference"), name="club_award_reference_unique"
            )
        ]
        indexes = [models.Index(fields=("membership", "-created_at"))]

    def __str__(self) -> str:
        return f"+{self.xp} XP to {self.membership}"


class CountedOccurrence(models.Model):
    """One occurrence that already moved one member's progress on one mission.

    The award's own reference only covers the event that *finished* a run, so
    without this a mission needing three events could be finished by one event
    delivered three times. Written only when the caller gave a reference: an
    occurrence without one cannot be told apart from a second, real one.
    """

    id = models.BigAutoField(primary_key=True)
    progress = models.ForeignKey(MissionProgress, on_delete=models.CASCADE, related_name="counted")
    reference = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("progress", "reference"), name="club_counted_reference_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.progress}: {self.reference}"
