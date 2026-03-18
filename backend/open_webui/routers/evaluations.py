import json
import logging
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from open_webui.models.users import Users, UserModel
from open_webui.models.feedbacks import (
    FeedbackIdResponse,
    FeedbackModel,
    FeedbackResponse,
    FeedbackForm,
    FeedbackUserResponse,
    FeedbackListResponse,
    LeaderboardFeedbackData,
    ModelHistoryEntry,
    ModelHistoryResponse,
    Feedbacks,
    RatingData,
    SnapshotData,
)
from open_webui.models.chats import Chats

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.internal.db import get_session
from sqlalchemy.orm import Session
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT

log = logging.getLogger(__name__)

# Default judge system prompt: ask for JSON only
DEFAULT_JUDGE_SYSTEM_PROMPT = """You are a judge. Given the user message and the assistant reply, output only a JSON object with exactly these keys: "rating" (integer 1 or -1: 1 = good, -1 = bad) and "reason" (short string). No other text."""

# OpenAI-compatible judge call (works with OpenRouter). Returns (rating, reason) or (None, error_message).
async def _call_judge_model(request: Request, model_id: str, system_prompt: str, user_content: str, user: UserModel):
    from open_webui.routers.openai import get_all_models, get_headers_and_cookies

    models = getattr(request.app.state, "OPENAI_MODELS", None) or {}
    if not models or model_id not in models:
        await get_all_models(request, user=user)
        models = request.app.state.OPENAI_MODELS or {}
    model = models.get(model_id)
    if not model:
        return None, "Model not found"

    idx = model.get("urlIdx", 0)
    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),
    )
    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, user=user
    )
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 256,
        "stream": False,
    }
    request_url = f"{url}/chat/completions"
    try:
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT or 60),
        ) as session:
            async with session.post(
                request_url,
                json=payload,
                headers=headers,
                cookies=cookies,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                if r.status >= 400:
                    text = await r.text()
                    return None, f"HTTP {r.status}: {text[:200]}"
                data = await r.json()
    except Exception as e:
        log.exception(e)
        return None, str(e)

    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    # Extract JSON object (first { ... } with balanced braces)
    start = content.find("{")
    if start == -1:
        return None, "Could not parse JSON from judge response"
    depth = 0
    end = -1
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None, "Could not parse JSON from judge response"
    try:
        obj = json.loads(content[start:end])
        rating = obj.get("rating")
        reason = obj.get("reason", "")
        if rating not in (1, -1, "1", "-1"):
            return None, f"Invalid rating value: {rating}"
        return (str(rating) if isinstance(rating, int) else rating), (reason or "")
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"


router = APIRouter()


# Leaderboard Elo Rating Computation
#
# How it works:
# 1. Each model starts with a rating of 1000
# 2. When a user picks a winner between two models, ratings are adjusted:
#    - Winner gains points, loser loses points
#    - The amount depends on expected outcome (upset = bigger change)
# 3. The Elo formula: new_rating = old_rating + K * (actual - expected)
#    - K=32 controls how much ratings can change per match
#    - expected = probability of winning based on current ratings
#
# Query-based re-ranking (optional):
#    When a user searches for a topic (e.g., "coding"), we want to show
#    which models perform best FOR THAT TOPIC. We do this by:
#    1. Computing semantic similarity between the query and each feedback's tags
#    2. Using that similarity as a weight in the Elo calculation
#    3. Feedbacks about "coding" contribute more to the final ranking
#    4. Feedbacks about unrelated topics (e.g., "cooking") contribute less
#    This gives topic-specific leaderboards without needing separate data.

import os

EMBEDDING_MODEL_NAME = os.environ.get(
    "AUXILIARY_EMBEDDING_MODEL", "TaylorAI/bge-micro-v2"
)
_embedding_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as e:
            log.error(f"Embedding model load failed: {e}")
    return _embedding_model


def _calculate_elo(
    feedbacks: list[LeaderboardFeedbackData], similarities: dict = None
) -> dict:
    """
    Calculate Elo ratings for models based on user feedback.

    Each feedback represents a comparison where a user rated one model
    against its opponents (sibling_model_ids). Rating=1 means the model won,
    rating=-1 means it lost.

    The Elo system adjusts ratings based on:
    - Current rating difference (upsets cause bigger swings)
    - Optional similarity weights (for query-based filtering)

    Returns: {model_id: {"rating": float, "won": int, "lost": int}}
    """
    K_FACTOR = 32  # Standard Elo K-factor for rating volatility
    model_stats = {}

    def get_or_create_stats(model_id):
        if model_id not in model_stats:
            model_stats[model_id] = {"rating": 1000.0, "won": 0, "lost": 0}
        return model_stats[model_id]

    for feedback in feedbacks:
        data = feedback.data or {}
        winner_id = data.get("model_id")
        rating_value = str(data.get("rating", ""))
        if not winner_id or rating_value not in ("1", "-1"):
            continue

        won = rating_value == "1"
        weight = similarities.get(feedback.id, 1.0) if similarities else 1.0

        for opponent_id in data.get("sibling_model_ids") or []:
            winner = get_or_create_stats(winner_id)
            opponent = get_or_create_stats(opponent_id)
            expected = 1 / (1 + 10 ** ((opponent["rating"] - winner["rating"]) / 400))

            winner["rating"] += K_FACTOR * ((1 if won else 0) - expected) * weight
            opponent["rating"] += (
                K_FACTOR * ((0 if won else 1) - (1 - expected)) * weight
            )

            if won:
                winner["won"] += 1
                opponent["lost"] += 1
            else:
                winner["lost"] += 1
                opponent["won"] += 1

    return model_stats


def _get_top_tags(feedbacks: list[LeaderboardFeedbackData], limit: int = 5) -> dict:
    """
    Count tag occurrences per model and return the most frequent ones.

    Each feedback can have tags describing the conversation topic.
    This aggregates those tags per model to show what topics each model
    is commonly used for.

    Returns: {model_id: [{"tag": str, "count": int}, ...]}
    """
    from collections import defaultdict

    tag_counts = defaultdict(lambda: defaultdict(int))

    for feedback in feedbacks:
        data = feedback.data or {}
        model_id = data.get("model_id")
        if model_id:
            for tag in data.get("tags", []):
                tag_counts[model_id][tag] += 1

    return {
        model_id: [
            {"tag": tag, "count": count}
            for tag, count in sorted(tags.items(), key=lambda x: -x[1])[:limit]
        ]
        for model_id, tags in tag_counts.items()
    }


def _compute_similarities(feedbacks: list[LeaderboardFeedbackData], query: str) -> dict:
    """
    Compute how relevant each feedback is to a search query.

    Uses embeddings to find semantic similarity between the query and
    each feedback's tags. Higher similarity means the feedback is more
    relevant to what the user searched for.

    This is used to weight Elo calculations - feedbacks matching the
    query have more influence on the final rankings.

    Returns: {feedback_id: similarity_score (0-1)}
    """
    import numpy as np

    embedding_model = _get_embedding_model()
    if not embedding_model:
        return {}

    all_tags = list(
        {
            tag
            for feedback in feedbacks
            if feedback.data
            for tag in feedback.data.get("tags", [])
        }
    )
    if not all_tags:
        return {}

    try:
        tag_embeddings = embedding_model.encode(all_tags)
        query_embedding = embedding_model.encode([query])[0]
    except Exception as e:
        log.error(f"Embedding error: {e}")
        return {}

    # Vectorized cosine similarity
    tag_norms = np.linalg.norm(tag_embeddings, axis=1)
    query_norm = np.linalg.norm(query_embedding)
    similarities = np.dot(tag_embeddings, query_embedding) / (
        tag_norms * query_norm + 1e-9
    )
    tag_similarity_map = dict(zip(all_tags, similarities.tolist()))

    return {
        feedback.id: max(
            (
                tag_similarity_map.get(tag, 0)
                for tag in (feedback.data or {}).get("tags", [])
            ),
            default=0,
        )
        for feedback in feedbacks
    }


class LeaderboardEntry(BaseModel):
    model_id: str
    rating: int
    won: int
    lost: int
    count: int
    top_tags: list[dict]


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    query: Optional[str] = None,
    user=Depends(get_admin_user),
    db: Session = Depends(get_session),
):
    """Get model leaderboard with Elo ratings. Query filters by tag similarity."""
    feedbacks = Feedbacks.get_feedbacks_for_leaderboard(db=db)

    similarities = None
    if query and query.strip():
        similarities = await run_in_threadpool(
            _compute_similarities, feedbacks, query.strip()
        )

    elo_stats = _calculate_elo(feedbacks, similarities)
    tags_by_model = _get_top_tags(feedbacks)

    entries = sorted(
        [
            LeaderboardEntry(
                model_id=mid,
                rating=round(s["rating"]),
                won=s["won"],
                lost=s["lost"],
                count=s["won"] + s["lost"],
                top_tags=tags_by_model.get(mid, []),
            )
            for mid, s in elo_stats.items()
        ],
        key=lambda e: e.rating,
        reverse=True,
    )

    return LeaderboardResponse(entries=entries)


@router.get("/leaderboard/{model_id}/history", response_model=ModelHistoryResponse)
async def get_model_history(
    model_id: str,
    days: int = 30,
    user=Depends(get_admin_user),
    db: Session = Depends(get_session),
):
    """Get daily win/loss history for a specific model."""
    history = Feedbacks.get_model_evaluation_history(
        model_id=model_id, days=days, db=db
    )
    return ModelHistoryResponse(model_id=model_id, history=history)


############################
# GetConfig
############################


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_EVALUATION_ARENA_MODELS": request.app.state.config.ENABLE_EVALUATION_ARENA_MODELS,
        "EVALUATION_ARENA_MODELS": request.app.state.config.EVALUATION_ARENA_MODELS,
    }


############################
# UpdateConfig
############################


class UpdateConfigForm(BaseModel):
    ENABLE_EVALUATION_ARENA_MODELS: Optional[bool] = None
    EVALUATION_ARENA_MODELS: Optional[list[dict]] = None


@router.post("/config")
async def update_config(
    request: Request,
    form_data: UpdateConfigForm,
    user=Depends(get_admin_user),
):
    config = request.app.state.config
    if form_data.ENABLE_EVALUATION_ARENA_MODELS is not None:
        config.ENABLE_EVALUATION_ARENA_MODELS = form_data.ENABLE_EVALUATION_ARENA_MODELS
    if form_data.EVALUATION_ARENA_MODELS is not None:
        config.EVALUATION_ARENA_MODELS = form_data.EVALUATION_ARENA_MODELS
    return {
        "ENABLE_EVALUATION_ARENA_MODELS": config.ENABLE_EVALUATION_ARENA_MODELS,
        "EVALUATION_ARENA_MODELS": config.EVALUATION_ARENA_MODELS,
    }


@router.get("/feedbacks/all", response_model=list[FeedbackResponse])
async def get_all_feedbacks(
    user=Depends(get_admin_user), db: Session = Depends(get_session)
):
    feedbacks = Feedbacks.get_all_feedbacks(db=db)
    return feedbacks


@router.get("/feedbacks/all/ids", response_model=list[FeedbackIdResponse])
async def get_all_feedback_ids(
    user=Depends(get_admin_user), db: Session = Depends(get_session)
):
    return Feedbacks.get_all_feedback_ids(db=db)


@router.delete("/feedbacks/all")
async def delete_all_feedbacks(
    user=Depends(get_admin_user), db: Session = Depends(get_session)
):
    success = Feedbacks.delete_all_feedbacks(db=db)
    return success


@router.get("/feedbacks/all/export", response_model=list[FeedbackModel])
async def export_all_feedbacks(
    user=Depends(get_admin_user), db: Session = Depends(get_session)
):
    feedbacks = Feedbacks.get_all_feedbacks(db=db)
    return feedbacks


@router.get("/feedbacks/user", response_model=list[FeedbackUserResponse])
async def get_feedbacks(
    user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    feedbacks = Feedbacks.get_feedbacks_by_user_id(user.id, db=db)
    return feedbacks


@router.delete("/feedbacks", response_model=bool)
async def delete_feedbacks(
    user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    success = Feedbacks.delete_feedbacks_by_user_id(user.id, db=db)
    return success


PAGE_ITEM_COUNT = 30


class JudgeRequestForm(BaseModel):
    chat_id: str
    message_ids: list[str]
    judge_model_id: Optional[str] = None
    judge_system_prompt: Optional[str] = None


class JudgeResultItem(BaseModel):
    message_id: str
    feedback_id: Optional[str] = None
    rating: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None


class JudgeResponse(BaseModel):
    evaluated: int
    failed: int
    feedback_ids: list[str]
    results: list[JudgeResultItem]


class RejectResponseItem(BaseModel):
    chat_id: str
    message_id: str
    user_content: str
    assistant_content: str
    model_id: str
    chat_title: str
    user_id: str
    updated_at: int


@router.get("/reject-responses", response_model=list[RejectResponseItem])
async def get_reject_responses(
    user_id: Optional[str] = None,
    keywords: Optional[str] = None,
    chat_limit: Optional[int] = 150,
    result_limit: Optional[int] = 300,
    user=Depends(get_admin_user),
    db: Session = Depends(get_session),
):
    """
    List assistant messages that look like policy refusals (content-based filter).
    No DB flag needed. Optional: user_id (filter by owner), keywords (comma-separated).
    """
    keyword_list = None
    if keywords:
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    items = Chats.get_reject_response_items(
        user_id=user_id,
        keywords=keyword_list,
        chat_limit=chat_limit or 150,
        result_limit=result_limit or 300,
        db=db,
    )
    return [RejectResponseItem(**x) for x in items]


@router.post("/judge", response_model=JudgeResponse)
async def run_judge(
    request: Request,
    form_data: JudgeRequestForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    """Run LLM-as-judge on selected assistant messages. Uses OpenAI-compatible API (e.g. OpenRouter)."""
    chat = Chats.get_chat_by_id_and_user_id(form_data.chat_id, user.id, db=db)
    if not chat and getattr(user, "role", None) == "admin":
        chat = Chats.get_chat_by_id(form_data.chat_id, db=db)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found or access denied",
        )

    history = (chat.chat or {}).get("history", {}) or {}
    messages = history.get("messages", {}) or {}
    judge_model_id = form_data.judge_model_id
    if not judge_model_id:
        from open_webui.routers.openai import get_all_models
        await get_all_models(request, user=user)
        openai_models = getattr(request.app.state, "OPENAI_MODELS", None) or {}
        judge_model_id = next(iter(openai_models.keys()), None)
        if not judge_model_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No judge model configured. Add an OpenAI-compatible connection (e.g. OpenRouter) in Settings.",
            )
    system_prompt = form_data.judge_system_prompt or DEFAULT_JUDGE_SYSTEM_PROMPT

    feedback_ids = []
    results = []
    evaluated = 0
    failed = 0

    for message_id in form_data.message_ids:
        msg = messages.get(message_id)
        if not msg or msg.get("role") != "assistant":
            results.append(
                JudgeResultItem(message_id=message_id, error="Not an assistant message")
            )
            failed += 1
            continue
        parent_id = msg.get("parentId") or msg.get("parent_id")
        parent = messages.get(parent_id, {}) if parent_id else {}
        user_content = parent.get("content") or ""
        assistant_content = msg.get("content") or ""
        model_id = msg.get("model") or ""
        judge_user_content = (
            f"User message:\n{user_content}\n\nAssistant reply:\n{assistant_content}\n\n"
            f"Model: {model_id}. Rate 1 (good) or -1 (bad) and give a short reason."
        )
        rating, reason_or_error = await _call_judge_model(
            request, judge_model_id, system_prompt, judge_user_content, user
        )
        if rating is None:
            results.append(
                JudgeResultItem(
                    message_id=message_id,
                    error=reason_or_error,
                )
            )
            failed += 1
            continue
        meta = {
            "chat_id": form_data.chat_id,
            "message_id": message_id,
            "source": "llm_judge",
        }
        form = FeedbackForm(
            type="rating",
            data=RatingData(rating=rating, reason=reason_or_error, model_id=model_id),
            meta=meta,
            snapshot=SnapshotData(chat=chat.chat),
        )
        feedback = Feedbacks.insert_new_feedback(
            user_id=user.id, form_data=form, db=db
        )
        if feedback:
            feedback_ids.append(feedback.id)
            evaluated += 1
            results.append(
                JudgeResultItem(
                    message_id=message_id,
                    feedback_id=feedback.id,
                    rating=rating,
                    reason=reason_or_error,
                )
            )
        else:
            results.append(
                JudgeResultItem(message_id=message_id, error="Failed to save feedback")
            )
            failed += 1

    return JudgeResponse(
        evaluated=evaluated,
        failed=failed,
        feedback_ids=feedback_ids,
        results=results,
    )


@router.get("/feedbacks/list", response_model=FeedbackListResponse)
async def get_feedbacks(
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    chat_id: Optional[str] = None,
    source: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    model_id: Optional[str] = None,
    rating: Optional[str] = None,
    user=Depends(get_admin_user),
    db: Session = Depends(get_session),
):
    limit = PAGE_ITEM_COUNT

    page = max(1, page)
    skip = (page - 1) * limit

    filter = {}
    if order_by:
        filter["order_by"] = order_by
    if direction:
        filter["direction"] = direction
    if chat_id is not None:
        filter["chat_id"] = chat_id
    if source is not None:
        filter["source"] = source
    if date_from is not None:
        filter["date_from"] = date_from
    if date_to is not None:
        filter["date_to"] = date_to
    if model_id is not None:
        filter["model_id"] = model_id
    if rating is not None:
        filter["rating"] = rating

    result = Feedbacks.get_feedback_items(filter=filter, skip=skip, limit=limit, db=db)
    return result


@router.post("/feedback", response_model=FeedbackModel)
async def create_feedback(
    request: Request,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    feedback = Feedbacks.insert_new_feedback(
        user_id=user.id, form_data=form_data, db=db
    )
    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    return feedback


@router.get("/feedback/{id}", response_model=FeedbackModel)
async def get_feedback_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    if user.role == "admin":
        feedback = Feedbacks.get_feedback_by_id(id=id, db=db)
    else:
        feedback = Feedbacks.get_feedback_by_id_and_user_id(
            id=id, user_id=user.id, db=db
        )

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return feedback


@router.post("/feedback/{id}", response_model=FeedbackModel)
async def update_feedback_by_id(
    id: str,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    if user.role == "admin":
        feedback = Feedbacks.update_feedback_by_id(id=id, form_data=form_data, db=db)
    else:
        feedback = Feedbacks.update_feedback_by_id_and_user_id(
            id=id, user_id=user.id, form_data=form_data, db=db
        )

    if not feedback:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return feedback


@router.delete("/feedback/{id}")
async def delete_feedback_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    if user.role == "admin":
        success = Feedbacks.delete_feedback_by_id(id=id, db=db)
    else:
        success = Feedbacks.delete_feedback_by_id_and_user_id(
            id=id, user_id=user.id, db=db
        )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return success
