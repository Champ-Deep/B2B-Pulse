from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.automation import router as automation_router
from app.api.integrations import router as integrations_router
from app.api.org import router as org_router
from app.api.teams import router as teams_router
from app.api.tracked_pages import router as tracked_pages_router
from app.api.users import router as users_router
from app.api.webhooks import router as webhooks_router

api_router = APIRouter(prefix="/api")

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(tracked_pages_router)
api_router.include_router(integrations_router)
api_router.include_router(audit_router)
api_router.include_router(webhooks_router)
api_router.include_router(automation_router)
api_router.include_router(org_router)
api_router.include_router(teams_router)
api_router.include_router(admin_router)
