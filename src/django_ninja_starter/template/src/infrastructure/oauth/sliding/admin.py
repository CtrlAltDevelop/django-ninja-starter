from django.contrib import admin

from infrastructure.oauth.sliding.models import SlidingToken, SlidingTokenEvent

admin.site.register(SlidingToken)
admin.site.register(SlidingTokenEvent)
