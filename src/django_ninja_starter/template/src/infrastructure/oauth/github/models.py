from django.db import models

from infrastructure.oauth.core.models import AbstractSocialAccount


class GitHubAccount(AbstractSocialAccount):
    login = models.CharField(max_length=255, blank=True, db_index=True)

    class Meta(AbstractSocialAccount.Meta):
        verbose_name = "GitHub account"
