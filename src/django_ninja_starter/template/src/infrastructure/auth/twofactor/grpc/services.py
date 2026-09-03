"""Second factors, over gRPC.

Two of these actions -- ``Challenge`` and ``Verify`` -- take no credential.
A caller in the middle of a sign-in does not have one yet, and the login ticket
is what stands in for it. Everything else is an account acting on itself and
needs the usual ``authorization`` metadata.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.auth.core.grpc.messages import (
    CHALLENGE_RESPONSE,
    LOGIN_RESPONSE,
    MESSAGE_RESPONSE,
)
from infrastructure.auth.twofactor.grpc.serializers import EnrolledFactor
from infrastructure.auth.twofactor.services import SentCode, twofactor_service
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller

FACTOR_LIST_RESPONSE = [
    {"name": "methods", "cardinality": "repeated", "type": EnrolledFactor},
    {"name": "available", "cardinality": "repeated", "type": "string"},
    {"name": "unused_recovery_codes", "type": "int32"},
]
TOTP_ENROLMENT_RESPONSE = [
    {"name": "secret", "type": "string"},
    {"name": "otpauth_uri", "type": "string"},
]
RECOVERY_CODES_RESPONSE = [{"name": "codes", "cardinality": "repeated", "type": "string"}]
TICKET_CODE_REQUEST = [
    {"name": "ticket", "type": "string"},
    {"name": "code", "type": "string"},
]


def _pb2() -> Any:
    from infrastructure.auth.twofactor.grpc import auth_twofactor_pb2

    return auth_twofactor_pb2


def _challenge(sent: SentCode) -> Any:
    # Named `SentCode` on the wire, not `Challenge`: proto3 puts messages and rpc
    # methods in one namespace, and this service publishes a `Challenge` rpc.
    return _pb2().SentCode(
        ticket=sent.ticket,
        channel=sent.channel,
        destination=sent.destination,
        expires_in=sent.expires_in,
    )


class TwoFactorService(generics.GenericService):
    """The login-time challenge, and everything an account does to its factors."""

    @grpc_action(
        request=[
            {"name": "login_ticket", "type": "string"},
            {"name": "method", "type": "string"},
        ],
        request_name="ChallengeRequest",
        response=CHALLENGE_RESPONSE,
        response_name="SentCode",
    )
    @action
    async def Challenge(self, request: Any, context: Any) -> Any:
        sent = await sync_to_async(twofactor_service.challenge)(
            context.http_request,
            login_ticket=request.login_ticket,
            method=request.method,
        )
        return _challenge(sent)

    @grpc_action(
        request=[
            {"name": "login_ticket", "type": "string"},
            {"name": "code", "type": "string"},
            {"name": "method", "type": "string"},
        ],
        request_name="VerifyRequest",
        response=LOGIN_RESPONSE,
        response_name="LoginResult",
    )
    @action
    async def Verify(self, request: Any, context: Any) -> Any:
        credentials = await sync_to_async(twofactor_service.verify)(
            context.http_request,
            login_ticket=request.login_ticket,
            code=request.code,
            method=request.method,
        )
        pb2 = _pb2()
        return pb2.LoginResult(
            requires_second_factor=False,
            credentials=pb2.Credentials(
                token_type=credentials.token_type,
                access_token=credentials.access_token,
                refresh_token=credentials.refresh_token,
                expires_in=credentials.expires_in or 0,
                session_id=credentials.session_id,
            ),
        )

    @grpc_action(request=[], response=FACTOR_LIST_RESPONSE, response_name="FactorList")
    @action
    async def Methods(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        factors = await sync_to_async(twofactor_service.factors)(user)
        pb2 = _pb2()
        return pb2.FactorList(
            methods=[
                pb2.EnrolledFactor(
                    method=factor.method,
                    destination=factor.destination,
                    confirmed=factor.confirmed,
                    last_used_at=factor.last_used_at,
                )
                for factor in factors.methods
            ],
            available=factors.available,
            unused_recovery_codes=factors.unused_recovery_codes,
        )

    @grpc_action(request=[], response=TOTP_ENROLMENT_RESPONSE, response_name="TotpEnrolment")
    @action
    async def TotpEnroll(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        enrolment = await sync_to_async(twofactor_service.start_totp)(user)
        return _pb2().TotpEnrolment(secret=enrolment.secret, otpauth_uri=enrolment.otpauth_uri)

    @grpc_action(
        request=[{"name": "code", "type": "string"}],
        request_name="TotpConfirmRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def TotpConfirm(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(twofactor_service.confirm_totp)(
            context.http_request, user, code=request.code
        )
        return _pb2().Message(detail=detail)

    @grpc_action(
        request=[{"name": "phone", "type": "string"}],
        request_name="SmsEnrollRequest",
        response=CHALLENGE_RESPONSE,
        response_name="SentCode",
    )
    @action
    async def SmsEnroll(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _challenge(
            await sync_to_async(twofactor_service.start_sms)(user, phone=request.phone)
        )

    @grpc_action(
        request=TICKET_CODE_REQUEST,
        request_name="SmsConfirmRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def SmsConfirm(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(twofactor_service.confirm_sms)(
            context.http_request, user, ticket=request.ticket, code=request.code
        )
        return _pb2().Message(detail=detail)

    @grpc_action(request=[], response=CHALLENGE_RESPONSE, response_name="SentCode")
    @action
    async def EmailEnroll(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _challenge(await sync_to_async(twofactor_service.start_email)(user))

    @grpc_action(
        request=TICKET_CODE_REQUEST,
        request_name="EmailConfirmRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def EmailConfirm(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(twofactor_service.confirm_email)(
            context.http_request, user, ticket=request.ticket, code=request.code
        )
        return _pb2().Message(detail=detail)

    @grpc_action(request=[], response=RECOVERY_CODES_RESPONSE, response_name="RecoveryCodes")
    @action
    async def RecoveryGenerate(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        codes = await sync_to_async(twofactor_service.generate_recovery_codes)(
            context.http_request, user
        )
        return _pb2().RecoveryCodes(codes=codes)

    @grpc_action(
        request=[{"name": "method", "type": "string"}],
        request_name="RemoveRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def Remove(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(twofactor_service.remove)(
            context.http_request, user, method=request.method
        )
        return _pb2().Message(detail=detail)


GRPC_SERVICES = [TwoFactorService]
