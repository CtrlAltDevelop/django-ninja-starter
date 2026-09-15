from typing import Any

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def award_to_membership_club(apps: Any, schema_editor: Any) -> None:
    """Every award so far was earned in the club its membership is in now.

    True up to this migration, because until it a membership's awards were
    never told apart by club -- which is the bug it exists to stop.
    """
    XpAward = apps.get_model("club", "XpAward")
    Membership = apps.get_model("club", "Membership")
    XpAward.objects.update(
        club_id=Subquery(Membership.objects.filter(pk=OuterRef("membership_id")).values("club_id"))
    )


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="xpaward",
            name="club",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="awards",
                to="club.club",
            ),
        ),
        migrations.RunPython(award_to_membership_club, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="xpaward",
            name="club",
            field=models.ForeignKey(
                help_text=(
                    "The club this was earned in. A member who moves clubs does not "
                    "carry one ladder's XP up another."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="awards",
                to="club.club",
            ),
        ),
        migrations.CreateModel(
            name="CountedOccurrence",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("reference", models.CharField(max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "progress",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="counted",
                        to="club.missionprogress",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("progress", "reference"), name="club_counted_reference_unique"
                    )
                ],
            },
        ),
    ]
