from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backend.models.schemas import ErrorResponse
from app.backend.services import llm_preferences

router = APIRouter(prefix="/user-settings", tags=["user-settings"])


class ModelPreferenceRequest(BaseModel):
    model_provider: str = Field(..., min_length=1, max_length=64)
    model_name: str = Field(..., min_length=1, max_length=200)


class ModelPreferenceResponse(BaseModel):
    model_provider: str
    model_name: str
    preference_saved: bool


@router.get(
    "/model",
    response_model=ModelPreferenceResponse,
    responses={500: {"model": ErrorResponse, "description": "Internal server error"}},
)
async def get_model_preference():
    pref = llm_preferences.get_model_preference()
    return ModelPreferenceResponse(
        model_provider=pref.model_provider,
        model_name=pref.model_name,
        preference_saved=pref.preference_saved,
    )


@router.put(
    "/model",
    response_model=ModelPreferenceResponse,
    responses={400: {"model": ErrorResponse, "description": "Invalid model preference"}},
)
async def set_model_preference(request: ModelPreferenceRequest):
    try:
        pref = llm_preferences.set_model_preference(request.model_provider, request.model_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ModelPreferenceResponse(
        model_provider=pref.model_provider,
        model_name=pref.model_name,
        preference_saved=pref.preference_saved,
    )
