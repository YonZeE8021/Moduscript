"""Plan mode API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth.deps import CurrentUser, get_current_user
from plan.schemas import (
    KnowledgeL1Update,
    PlanMetaUpdate,
    PlanQuestionRegenerate,
    PlanReferenceLookup,
    PlanSessionCreate,
    PlanTurnRegenerate,
    PlanTurnSubmit,
)
from plan.service import plan_service
from session_service import session_service, sse_encode

router = APIRouter(prefix="/api/v1/plan", tags=["plan"])


@router.get("/sessions")
async def list_plan_sessions(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    recycled: bool = Query(False),
):
    items = await plan_service.list_plans(user.id, recycled=recycled)
    return {"items": items}


@router.post("/sessions")
async def create_plan_session(body: PlanSessionCreate, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        data = await plan_service.create_plan(
            user.id,
            context=body.context.model_dump(),
            task_title=body.task_title,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return data


@router.get("/sessions/{plan_id}")
async def get_plan_session(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    data = await plan_service.get_plan(user.id, plan_id)
    if not data:
        raise HTTPException(status_code=404, detail="plan not found")
    return data


@router.patch("/sessions/{plan_id}")
async def update_plan_session(
    plan_id: str,
    body: PlanMetaUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    if body.task_title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        return await plan_service.update_plan_meta(
            user.id, plan_id, task_title=body.task_title, pinned=body.pinned
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/trash")
async def trash_plan_session(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.trash_plan(user.id, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/restore")
async def restore_plan_session(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.restore_plan(user.id, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{plan_id}/events")
async def plan_events(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    plan = await plan_service.get_plan(user.id, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")

    async def gen():
        async for event in plan_service.event_stream(user.id, plan_id):
            yield sse_encode(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{plan_id}/turns")
async def submit_plan_turn(
    plan_id: str,
    body: PlanTurnSubmit,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    try:
        return await plan_service.submit_turn(user.id, plan_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/finalize")
async def finalize_plan(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.finalize(user.id, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/handoff")
async def handoff_plan(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.handoff(user.id, plan_id, session_service=session_service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/sessions/{plan_id}/knowledge-l1")
async def update_plan_knowledge_l1(
    plan_id: str,
    body: KnowledgeL1Update,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    try:
        return await plan_service.update_knowledge_l1(user.id, plan_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/skip-reference-wait")
async def skip_plan_reference_wait(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.skip_reference_wait(user.id, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/references/{project_id}/retry")
async def retry_plan_reference(
    plan_id: str,
    project_id: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    try:
        return await plan_service.retry_reference_materialize(user.id, plan_id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/turns/retry-first")
async def retry_first_plan_turn(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.retry_first_turn(user.id, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/turns/regenerate")
async def regenerate_plan_turn(
    plan_id: str,
    body: PlanTurnRegenerate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    try:
        return await plan_service.regenerate_turn(
            user.id, plan_id, body.instruction, temperature=body.temperature
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/turns/questions/{question_id}/regenerate")
async def regenerate_plan_question(
    plan_id: str,
    question_id: str,
    body: PlanQuestionRegenerate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    try:
        return await plan_service.regenerate_question(
            user.id, plan_id, question_id, action=body.action
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{plan_id}/references")
async def get_plan_references(plan_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    try:
        return await plan_service.get_references(user.id, plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{plan_id}/references/{project_id}/lookup")
async def lookup_plan_reference(
    plan_id: str,
    project_id: str,
    body: PlanReferenceLookup,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    try:
        return await plan_service.lookup_reference(user.id, plan_id, project_id, body.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
