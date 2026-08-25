from django.contrib import admin

from infrastructure.auth.twofactor.models import RecoveryCode, SecondFactor

admin.site.register(SecondFactor)
admin.site.register(RecoveryCode)
