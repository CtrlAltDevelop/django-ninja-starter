from django.db import models

from infrastructure.oauth.core.models import AbstractSocialAccount


class MicrosoftAccount(AbstractSocialAccount):
    tenant_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta(AbstractSocialAccount.Meta):
        verbose_name = "Microsoft account"
