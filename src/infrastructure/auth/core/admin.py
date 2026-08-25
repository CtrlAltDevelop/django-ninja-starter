from django.contrib import admin

from infrastructure.auth.core.models import AuthEvent, PhoneNumber

admin.site.register(PhoneNumber)
admin.site.register(AuthEvent)
