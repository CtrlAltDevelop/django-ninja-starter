from django.db import models

from infrastructure.oauth.core.models import AbstractSocialAccount


class AppleAccount(AbstractSocialAccount):
    is_private_email = models.BooleanField(default=False)
    real_user_status = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta(AbstractSocialAccount.Meta):
        verbose_name = "Apple account"
