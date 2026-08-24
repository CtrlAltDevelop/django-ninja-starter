from django.contrib import admin

from infrastructure.oauth_google.models import GoogleAccount

admin.site.register(GoogleAccount)
