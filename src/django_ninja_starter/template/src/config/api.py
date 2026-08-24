from ninja import NinjaAPI

from infrastructure.common.api import router as common_router

api = NinjaAPI(
    title="{{ project_title }} API",
    version="1.0.0",
    urls_namespace="api-v1",
    docs_url="/docs",
)

api.add_router("/health", common_router, tags=["Health"])
