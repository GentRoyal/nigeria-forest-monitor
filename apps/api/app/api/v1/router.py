from fastapi import APIRouter

from .api_keys import router as api_keys_router
from .assets import router as assets_router
from .audit import router as audit_router
from .auth import router as auth_router
from .events import router as events_router
from .exports import router as exports_router
from .invitations import router as invitations_router
from .jobs import router as jobs_router
from .members import router as members_router
from .notifications import router as notifications_router
from .observations import router as observations_router
from .organisation import router as organisation_router
from .sites import router as sites_router
from .subscriptions import router as subscriptions_router
from .timeline import router as timeline_router

router = APIRouter(prefix="/api/v1")
router.include_router(audit_router)
router.include_router(auth_router)
router.include_router(organisation_router)
router.include_router(members_router)
router.include_router(invitations_router)
router.include_router(assets_router)
router.include_router(api_keys_router)
router.include_router(jobs_router)
router.include_router(notifications_router)
router.include_router(subscriptions_router)
router.include_router(observations_router)
router.include_router(events_router)
router.include_router(exports_router)
router.include_router(sites_router)
router.include_router(timeline_router)
