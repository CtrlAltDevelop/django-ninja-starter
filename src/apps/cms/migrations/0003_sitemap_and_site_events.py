"""A generated sitemap, and the dates whoever runs the site has to remember.

The sitemap columns are all judgement rather than data: the pages in the
document are worked out from what is published, and what is stored here is only
what a generator cannot know -- whether to publish one, what address the pages
hang off, and which live page has no business in a search index.

`SiteEvent` is the other half of running a site, and is the one table here that
holds nothing a reader ever sees: the domain that expires, the campaign that
starts on the first, the audit due in March. It exists because the person who
has to act on those dates is looking at this admin rather than at whichever
calendar the date was written down in.
"""

import uuid
from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0002_open_graph_and_choice_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="page",
            name="in_sitemap",
            field=models.BooleanField(
                default=True,
                help_text="Turn off for a page that is live but should not be indexed.",
                verbose_name="List in the sitemap",
            ),
        ),
        migrations.AddField(
            model_name="page",
            name="sitemap_changefreq",
            field=models.CharField(
                blank=True,
                choices=[
                    ("always", "Always"),
                    ("hourly", "Hourly"),
                    ("daily", "Daily"),
                    ("weekly", "Weekly"),
                    ("monthly", "Monthly"),
                    ("yearly", "Yearly"),
                    ("never", "Never"),
                ],
                help_text="Leave empty to use the site's default.",
                max_length=16,
                verbose_name="Change frequency",
            ),
        ),
        migrations.AddField(
            model_name="page",
            name="sitemap_priority",
            field=models.DecimalField(
                blank=True,
                decimal_places=1,
                help_text="Between 0.0 and 1.0. Leave empty to use the site's default.",
                max_digits=2,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.0")),
                    django.core.validators.MaxValueValidator(Decimal("1.0")),
                ],
                verbose_name="Priority",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="sitemap_base_url",
            field=models.URLField(
                blank=True,
                help_text="The address pages hang off, for example https://example.com. Blank uses the canonical URL above.",
                verbose_name="Sitemap base URL",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="sitemap_changefreq",
            field=models.CharField(
                choices=[
                    ("always", "Always"),
                    ("hourly", "Hourly"),
                    ("daily", "Daily"),
                    ("weekly", "Weekly"),
                    ("monthly", "Monthly"),
                    ("yearly", "Yearly"),
                    ("never", "Never"),
                ],
                default="weekly",
                help_text="Used for any page that does not set its own.",
                max_length=16,
                verbose_name="Default change frequency",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="sitemap_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Turn off to answer the sitemap URL with a 404, for a site behind a login.",
                verbose_name="Publish a sitemap",
            ),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="sitemap_priority",
            field=models.DecimalField(
                decimal_places=1,
                default=Decimal("0.5"),
                help_text="Between 0.0 and 1.0, and only ever compared with this site's own pages.",
                max_digits=2,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.0")),
                    django.core.validators.MaxValueValidator(Decimal("1.0")),
                ],
                verbose_name="Default priority",
            ),
        ),
        migrations.CreateModel(
            name="SiteEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Hidden rows stay in the admin and disappear from the API.",
                        verbose_name="Shown",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="What has to happen.", max_length=200, verbose_name="Event"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("renewal", "Renewal"),
                            ("campaign", "Campaign"),
                            ("deadline", "Deadline"),
                            ("content", "Content refresh"),
                            ("other", "Other"),
                        ],
                        default="other",
                        max_length=16,
                    ),
                ),
                (
                    "happens_on",
                    models.DateField(
                        help_text="The day it happens, or the deadline it has to be done by.",
                        verbose_name="Date",
                    ),
                ),
                (
                    "repeats_yearly",
                    models.BooleanField(
                        default=False,
                        help_text="For a date that comes round every year, like a renewal.",
                    ),
                ),
                (
                    "remind_days_before",
                    models.PositiveSmallIntegerField(
                        default=14,
                        help_text="How much warning the dashboard gives. Zero means on the day.",
                        verbose_name="Remind this many days before",
                    ),
                ),
                (
                    "notes",
                    models.TextField(
                        blank=True, help_text="What whoever picks this up needs to know."
                    ),
                ),
                (
                    "url",
                    models.URLField(
                        blank=True, help_text="Where the work gets done: a registrar, a doc."
                    ),
                ),
                (
                    "is_done",
                    models.BooleanField(
                        default=False,
                        help_text="Ticked off. A yearly event comes back on its own once the date has passed.",
                        verbose_name="Handled",
                    ),
                ),
                ("handled_occurrence", models.DateField(blank=True, editable=False, null=True)),
            ],
            options={
                "verbose_name": "Site event",
                "verbose_name_plural": "Site events",
                "ordering": ("happens_on", "name"),
                "indexes": [
                    models.Index(
                        fields=["is_done", "happens_on"], name="cms_siteeve_is_done_29f1c9_idx"
                    )
                ],
            },
        ),
    ]
