from django.contrib import admin

from infrastructure.oauth_core.models import (
    OAuthAuditEvent,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthConsent,
    OAuthScope,
    SocialLoginAttempt,
)

admin.site.register(OAuthScope)
admin.site.register(OAuthClient)
admin.site.register(OAuthConsent)
admin.site.register(OAuthAuthorizationCode)
admin.site.register(OAuthAuditEvent)
admin.site.register(SocialLoginAttempt)
