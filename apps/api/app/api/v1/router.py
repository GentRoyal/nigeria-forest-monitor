from fastapi import APIRouter

from .auth import router as auth_router
from .invitations import router as invitations_router
from .members import router as members_router
from .organisation import router as organisation_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(organisation_router)
router.include_router(members_router)
router.include_router(invitations_router)
