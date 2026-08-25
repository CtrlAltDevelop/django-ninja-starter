from django.db import models

from infrastructure.oauth.core.models import AbstractSocialAccount


class GoogleAccount(AbstractSocialAccount):
    hosted_domain = models.CharField(max_length=255, blank=True)

    class Meta(AbstractSocialAccount.Meta):
        verbose_name = "Google account"
