from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.schemas import Out
from app.auth.preferences import PreferenceRepository
from app.deps import AppClock, CurrentUser, Db, ReadDb

router = APIRouter(prefix="/me/preferences", tags=["preferences"])


class PreferencesOut(Out):
    subtitle_language: str | None


class PreferencesIn(BaseModel):
    subtitle_language: str | None = Field(default=None, max_length=32)


@router.get("")
def get_preferences(user: CurrentUser, db: ReadDb, clock: AppClock) -> PreferencesOut:
    return PreferencesOut.model_validate(PreferenceRepository(db, clock).get(user.id))


@router.patch("")
def save_preferences(
    body: PreferencesIn, user: CurrentUser, db: Db, clock: AppClock
) -> PreferencesOut:
    saved = PreferenceRepository(db, clock).save(user.id, body.subtitle_language)
    return PreferencesOut.model_validate(saved)
