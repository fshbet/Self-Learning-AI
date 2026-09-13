"""Model calls with accounting (§29 model abstraction, §39 observability).

Every call goes through here so that model, prompt version, tokens, latency and
errors are recorded in ``llm_calls`` regardless of provider.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..adapters import get_llm
from ..config import get_settings
from ..models import LLMCall

log = logging.getLogger(__name__)


class LLMOutputError(RuntimeError):
    pass


def model_for(purpose: str) -> str:
    s = get_settings()
    return {
        "triage": s.llm_model_triage,
        "extract": s.llm_model_extract,
        "reason": s.llm_model_reason,
        "answer": s.llm_model_reason,
    }.get(purpose, s.llm_model_reason)


def _record(session: Session | None, **kwargs: Any) -> None:
    if session is None:
        return
    try:
        session.add(LLMCall(**kwargs))
        session.flush()
    except Exception:  # accounting must never break the pipeline
        log.exception("failed to record llm call")


def call_json(
    *,
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    session: Session | None = None,
    run_id: uuid.UUID | None = None,
    model: str | None = None,
    retries: int = 1,
) -> dict[str, Any]:
    llm = get_llm()
    model = model or model_for(purpose)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            res = llm.generate_json(system=system, user=user, model=model, schema=schema)
            data = json.loads(res.text)
            _record(
                session,
                run_id=run_id,
                provider=llm.name,
                model=res.model,
                purpose=purpose,
                prompt_tokens=res.prompt_tokens,
                completion_tokens=res.completion_tokens,
                latency_ms=res.latency_ms,
                ok=True,
            )
            return data
        except json.JSONDecodeError as exc:
            last_err = LLMOutputError(f"model returned invalid JSON: {exc}")
        except Exception as exc:
            last_err = exc
        _record(
            session,
            run_id=run_id,
            provider=llm.name,
            model=model,
            purpose=purpose,
            ok=False,
            error=str(last_err)[:2000],
        )
        log.warning("llm call failed (%s, attempt %d): %s", purpose, attempt + 1, last_err)
    assert last_err is not None
    raise last_err


def call_text(
    *,
    purpose: str,
    system: str,
    user: str,
    session: Session | None = None,
    run_id: uuid.UUID | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> str:
    llm = get_llm()
    model = model or model_for(purpose)
    try:
        res = llm.generate(system=system, user=user, model=model, temperature=temperature)
    except Exception as exc:
        _record(
            session, run_id=run_id, provider=llm.name, model=model, purpose=purpose, ok=False, error=str(exc)[:2000]
        )
        raise
    _record(
        session,
        run_id=run_id,
        provider=llm.name,
        model=res.model,
        purpose=purpose,
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        latency_ms=res.latency_ms,
        ok=True,
    )
    return res.text
