from django.contrib import admin

from infrastructure.oauth.apple.models import AppleAccount

admin.site.register(AppleAccount)
