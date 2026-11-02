"""REST API + static dashboard.

    python -m medgraph serve          (or: uvicorn medgraph.api.main:app)

Every /api route requires an API key with the right scope (read | chat | admin),
is rate limited, validates and sanitises its inputs, and runs graph queries
through a read-only client with server-side timeouts. Errors never expose
internals: they return {"error": {status, message, request_id}}.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import queries as q
from ..config import settings
from ..db import get_read_client
from ..observability import configure_logging, metrics, request_id_var
from ..ontology import get_ontology
from ..security.auth import Principal
from ..security.ratelimit import BusyError, ConcurrencyGate
from ..security.sanitize import TRIAL_STATUSES, InputError, clean_entity, clean_status, clean_text
from .security import RequestContextMiddleware, keystore, require

logger = logging.getLogger("medgraph.api")
WEB = Path(__file__).resolve().parent.parent / "web"

Name = Annotated[str, PathParam(min_length=1, max_length=120)]
Term = Annotated[str, Query(min_length=1, max_length=120)]
OptTerm = Annotated[Optional[str], Query(max_length=120)]
Limit = Annotated[int, Query(ge=1, le=50)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    problems = settings.problems()
    for p in problems:
        logger.warning("configuration problem: %s", p)
    if problems and settings.is_production:
        raise RuntimeError("refusing to start in production with configuration problems: " + "; ".join(problems))
    n = len(keystore())  # parse keys at startup so a bad MEDGRAPH_API_KEYS fails fast
    if settings.auth_disabled and not settings.is_production:
        logger.warning("authentication is DISABLED (development only)")
    logger.info("medgraph api starting: env=%s api_keys=%d", settings.env, n)
    yield
    get_read_client().close()


app = FastAPI(
    title="MedGraph", version="1.1.0", lifespan=lifespan,
    description="Medical knowledge graph + GraphRAG. Research use only — not medical advice.",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None, openapi_url=None if settings.is_production else "/openapi.json",
)
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "X-API-Key", "Content-Type"])
app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_body_bytes)
app.mount("/static", StaticFiles(directory=WEB), name="static")

_agent = None
_chat_gate = ConcurrencyGate(settings.agent_max_concurrency)


# --------------------------------------------------------------------------- errors
def _error(status: int, message: str, headers: dict | None = None, details=None) -> JSONResponse:
    body = {"error": {"status": status, "message": message, "request_id": request_id_var.get()}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException):
    return _error(exc.status_code, str(exc.detail), headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    # Report where and why, never echo the submitted value back.
    details = [{"field": ".".join(str(p) for p in e.get("loc", [])), "problem": e.get("msg")} for e in exc.errors()]
    return _error(422, "invalid request", details=details)


@app.exception_handler(InputError)
async def input_error(_request: Request, exc: InputError):
    return _error(422, str(exc))


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    logger.exception("unhandled error")
    metrics.inc("medgraph_unhandled_errors_total")
    return _error(500, "internal error")


def db():
    return get_read_client()


Read = Annotated[Principal, Depends(require("read"))]
Chat = Annotated[Principal, Depends(require("chat"))]
Admin = Annotated[Principal, Depends(require("admin"))]


# --------------------------------------------------------------------------- probes
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz():
    """Readiness: Neo4j reachable."""
    if not db().ping():
        return _error(503, "neo4j not reachable")
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def prometheus(_: Admin):
    return PlainTextResponse(metrics.render())


# --------------------------------------------------------------------------- graph
@app.get("/api/stats")
def stats(_: Admin):
    return q.stats(db())


@app.get("/api/ontology")
def ontology(_: Read):
    onto = get_ontology()
    return {
        "classes": [{"name": c.name, "key": c.key, "embedded": bool(c.embed_text), "comment": c.comment}
                    for c in onto.classes.values()],
        "relationships": [{"name": r.name, "domain": r.domain, "range": r.range, "comment": r.comment}
                          for r in onto.relationships.values()],
    }


@app.get("/api/drug/{name}")
def drug_profile(name: Name, _: Read):
    return q.drug_profile(db(), clean_entity(name, field="name"))


@app.get("/api/drug/{name}/safety")
def drug_safety(name: Name, _: Read, limit: Limit = 15):
    return q.drug_safety(db(), clean_entity(name, field="name"), limit=limit)


@app.get("/api/drug/{name}/similar")
def similar(name: Name, _: Read, limit: Limit = 10):
    return q.similar_drugs(db(), clean_entity(name, field="name"), limit=limit)


@app.get("/api/atc/{drug_or_code}")
def atc(drug_or_code: Name, _: Read):
    return q.decode_atc(db(), clean_entity(drug_or_code, field="drug_or_code"))


@app.get("/api/disease/{name}")
def disease(name: Name, _: Read, limit: Limit = 15):
    return q.disease_overview(db(), clean_entity(name, field="name"), limit=limit)


@app.get("/api/mesh/expand")
def mesh_expand(term: Term, _: Read, max_depth: Annotated[int, Query(ge=0, le=20)] = 10):
    return q.mesh_expand(db(), clean_entity(term, field="term"), max_depth=max_depth, max_terms=1000)


@app.get("/api/search")
def search(term: Term, _: Read, expand: bool = True, limit: Limit = 10):
    return q.search_documents(db(), clean_entity(term, field="term"), expand=expand, limit=limit)


@app.get("/api/trials")
def trials(_: Read, condition: OptTerm = None, drug: OptTerm = None,
           status: Annotated[Optional[str], Query(max_length=30, description=", ".join(TRIAL_STATUSES))] = None,
           limit: Limit = 20):
    if not condition and not drug:
        raise InputError("provide condition or drug")
    return q.trials_pipeline(db(), condition=clean_entity(condition, field="condition") if condition else None,
                             drug=clean_entity(drug, field="drug") if drug else None,
                             status=clean_status(status), limit=limit)


@app.get("/api/semantic")
def semantic(text: Annotated[str, Query(min_length=1, max_length=300)], _: Read,
             k: Annotated[int, Query(ge=1, le=20)] = 5):
    try:
        return q.semantic_search(db(), clean_text(text, 300, field="text"), k=k)
    except InputError:
        raise
    except Exception as exc:
        logger.warning("semantic search unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "semantic search is unavailable") from None


# --------------------------------------------------------------------------- assistant
class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=settings.history_turn_max_chars)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=settings.question_max_chars)
    history: list[Turn] = Field(default_factory=list, max_length=settings.history_max_turns * 2)


class ChatResponse(BaseModel):
    answer: str
    status: str
    tool_calls: list[dict]
    unverified_ids: list[str]
    duration_ms: int
    request_id: str


def get_agent():
    global _agent
    if _agent is None:
        from ..agent.agent import MedGraphAgent

        _agent = MedGraphAgent(db())
    return _agent


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, principal: Chat):
    try:
        with _chat_gate:
            agent = get_agent()
            result = agent.ask(req.question, [t.model_dump() for t in req.history], principal=principal.name)
    except BusyError:
        raise HTTPException(503, "assistant is busy, retry shortly", headers={"Retry-After": "5"}) from None
    except InputError:
        raise
    except Exception as exc:
        logger.warning("assistant unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "assistant unavailable") from None
    return ChatResponse(answer=result.answer, status=result.status,
                        tool_calls=[{"tool": c["tool"], "args": c["args"], "outcome": c["outcome"]}
                                    for c in result.tool_calls],
                        unverified_ids=result.unverified_ids, duration_ms=result.duration_ms,
                        request_id=request_id_var.get())
