from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator

from app.auth import require_admin_key, require_api_key
from app.config import load_actions, save_actions

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionOut(BaseModel):
    id: str
    label: str


class ActionIn(BaseModel):
    id: str
    label: str
    prompt_single: str
    prompt_sequence: Optional[str] = None

    @model_validator(mode="after")
    def default_sequence_prompt(self) -> "ActionIn":
        if not self.prompt_sequence:
            self.prompt_sequence = self.prompt_single
        return self


@router.get("", response_model=list[ActionOut], summary="List available actions")
async def list_actions(_: str = Depends(require_api_key)):
    return [ActionOut(id=a["id"], label=a["label"]) for a in load_actions()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ActionOut,
    summary="Add new action",
)
async def create_action(body: ActionIn, _: str = Depends(require_admin_key)):
    actions = load_actions()
    if any(a["id"] == body.id for a in actions):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Action '{body.id}' already exists")
    actions.append(body.model_dump())
    save_actions(actions)
    return ActionOut(id=body.id, label=body.label)


@router.put(
    "/{action_id}",
    response_model=ActionOut,
    summary="Update existing action",
)
async def update_action(action_id: str, body: ActionIn, _: str = Depends(require_admin_key)):
    actions = load_actions()
    for i, a in enumerate(actions):
        if a["id"] == action_id:
            actions[i] = body.model_dump()
            save_actions(actions)
            return ActionOut(id=body.id, label=body.label)
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Action '{action_id}' not found")


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove action")
async def delete_action(action_id: str, _: str = Depends(require_admin_key)):
    actions = load_actions()
    new_list = [a for a in actions if a["id"] != action_id]
    if len(new_list) == len(actions):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Action '{action_id}' not found")
    save_actions(new_list)
