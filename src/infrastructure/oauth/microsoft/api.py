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
