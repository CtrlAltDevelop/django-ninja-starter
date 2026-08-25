from django.http import HttpRequest, HttpResponse
from ninja import Router

from infrastructure.oauth.apple.provider import provider
from infrastructure.oauth.core.social import begin_social_login, finish_social_login

router = Router()


@router.get("/start", url_name="apple-oauth-start")
def start(request: HttpRequest) -> HttpResponse:
    return begin_social_login(request, provider)


@router.post("/callback", url_name="apple-oauth-callback")
def callback(request: HttpRequest) -> HttpResponse:
    return finish_social_login(request, provider, request.POST.dict())
