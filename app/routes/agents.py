"""The Agents page: every agent's prompt, and the person's own version and model."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import agents, profile
from ..auth import User, current_user

router = APIRouter(prefix="/api/agents")


class AgentRequest(BaseModel):
    prompt: str | None = None  # the person's version; "" goes back to the default
    model: str | None = None  # for agents whose model is chosen here; "" goes back to the default


@router.get("")
async def list_agents(user: User = Depends(current_user), mine_only: bool = False):
    """Every agent: what it does, when it runs, its default prompt, and the person's own.
    `mine_only` returns just the person's own prompts, {agent id: text} (small, for sign-in)."""
    if mine_only:
        return {"custom": (await profile.agent_settings(user.id))["prompts"]}
    return await agents.listing(user)


@router.put("/{agent_id}")
async def save_agent(agent_id: str, body: AgentRequest, user: User = Depends(current_user)):
    try:
        agents.check(agent_id, body.prompt, body.model, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await profile.save_agent(user.id, agent_id, body.prompt, body.model)
    return await agents.listing(user)
