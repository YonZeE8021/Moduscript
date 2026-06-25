"""Pydantic schemas for plan mode."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class KnowledgeL1(BaseModel):
    programming: int = Field(ge=0, le=2, default=1)
    ai_literacy: int = Field(ge=0, le=2, default=1)
    general_tech: int = Field(ge=0, le=2, default=0)
    mc_mechanics: int = Field(ge=0, le=2, default=0)
    completed_at: str | None = None


class PlanContext(BaseModel):
    user_concept: str
    minecraft_version: str = "1.20.1"
    mod_loader: str = "fabric"
    platform: str = "unspecified"
    knowledge_l1: KnowledgeL1 | None = None
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    requirements_detail: dict[str, Any] = Field(default_factory=dict)
    reference_mods: dict[str, Any] | None = None
    locale: str = "zh-CN"
    mod_name: str | None = None
    mod_id: str | None = None
    package_name: str | None = None
    interruption_level: int | None = None
    max_turns: int | None = None
    handoff_appendix: str | None = None
    plan_read_depth: Literal["fast", "standard", "deep"] = "standard"


class PlanSessionCreate(BaseModel):
    context: PlanContext
    task_title: str | None = None


class PlanTurnSubmit(BaseModel):
    answers: dict[str, str | list[str]] = Field(default_factory=dict)
    custom: dict[str, str] = Field(default_factory=dict)
    overall_remarks: str = ""
    freeform_message: str | None = None
    skip_questions: bool = False


class KnowledgeL1Update(BaseModel):
    programming: int = Field(ge=0, le=2)
    ai_literacy: int = Field(ge=0, le=2)
    general_tech: int = Field(ge=0, le=2)
    mc_mechanics: int = Field(ge=0, le=2)


class PlanTurnRegenerate(BaseModel):
    instruction: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)


class PlanQuestionRegenerate(BaseModel):
    action: Literal["expand", "replace"] = "replace"


class PlanReferenceLookup(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class PlanMetaUpdate(BaseModel):
    task_title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None


DifficultyLevel = Literal["low", "medium", "high", "extreme"]
PlanStatus = Literal["awaiting_l1", "active", "ready", "finalized", "handed_off"]
