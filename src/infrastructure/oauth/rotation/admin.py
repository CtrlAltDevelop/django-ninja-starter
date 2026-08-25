from django.contrib import admin

from infrastructure.oauth.rotation.models import (
    RefreshTokenReuseEvent,
    RotatingAccessToken,
    RotatingRefreshToken,
    TokenFamily,
)

admin.site.register(TokenFamily)
admin.site.register(RotatingRefreshToken)
admin.site.register(RotatingAccessToken)
admin.site.register(RefreshTokenReuseEvent)
