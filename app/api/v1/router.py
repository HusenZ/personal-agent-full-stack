from fastapi import APIRouter

from app.api.v1 import (
    analysis,
    auth,
    chat,
    conversations,
    memories,
    onboarding,
    plans,
    profile,
    runs,
    subscription,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(onboarding.router)
api_router.include_router(profile.router)
api_router.include_router(analysis.router)
api_router.include_router(plans.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
api_router.include_router(memories.router)
api_router.include_router(runs.router)
api_router.include_router(subscription.router)
