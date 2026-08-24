from django.http import HttpRequest, HttpResponse
from ninja import Router

from infrastructure.oauth_core.social import begin_social_login, finish_social_login
from infrastructure.oauth_google.provider import provider

router = Router()


@router.get("/start", url_name="google-oauth-start")
def start(request: HttpRequest) -> HttpResponse:
    return begin_social_login(request, provider)


@router.get("/callback", url_name="google-oauth-callback")
def callback(request: HttpRequest) -> HttpResponse:
    return finish_social_login(request, provider, request.GET.dict())
