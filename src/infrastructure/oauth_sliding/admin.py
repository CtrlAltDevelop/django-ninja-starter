from django.contrib import admin

from infrastructure.oauth_sliding.models import SlidingToken, SlidingTokenEvent

admin.site.register(SlidingToken)
admin.site.register(SlidingTokenEvent)
