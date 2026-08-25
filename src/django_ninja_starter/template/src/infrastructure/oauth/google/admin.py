from django.contrib import admin

from infrastructure.oauth.google.models import GoogleAccount

admin.site.register(GoogleAccount)
