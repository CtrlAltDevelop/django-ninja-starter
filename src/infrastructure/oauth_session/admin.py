from django.contrib import admin

from infrastructure.oauth_session.models import (
    OAuthSession,
    SessionAccessToken,
    SessionRevocation,
)

admin.site.register(OAuthSession)
admin.site.register(SessionAccessToken)
admin.site.register(SessionRevocation)
