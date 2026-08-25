from django.contrib import admin

from infrastructure.oauth.microsoft.models import MicrosoftAccount

admin.site.register(MicrosoftAccount)
