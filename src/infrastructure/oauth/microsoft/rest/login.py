"""The two browser redirects that are microsoft sign-in.

Both are ways in, so this app's whole REST surface is `login.py`. The flow
itself is :class:`SocialLoginService`'s; what is here is the part only a
browser can do -- follow a redirect, and carry the binding cookie back.
"""

from django.http import HttpRequest, HttpResponse
from ninja import Router

from infrastructure.oauth.core.social import begin_social_login, finish_social_login
from infrastructure.oauth.microsoft.provider import provider

router = Router()


@router.get("/start", url_name="microsoft-oauth-start")
def start(request: HttpRequest) -> HttpResponse:
    return begin_social_login(request, provider)


@router.get("/callback", url_name="microsoft-oauth-callback")
def callback(request: HttpRequest) -> HttpResponse:
    return finish_social_login(request, provider, request.GET.dict())
