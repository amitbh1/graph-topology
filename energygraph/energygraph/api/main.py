"""REST API + static dashboard.

    python -m energygraph serve           (or: uvicorn energygraph.api.main:app)

Every /api route requires credentials (API key or OIDC bearer token) with the
right scope, is rate limited, validates and sanitises inputs, and filters every
result by the caller's data entitlements. Errors never expose internals.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import queries as q
from ..config import settings
from ..db import close_read_client, get_read_client
from ..observability import configure_logging, metrics, request_id_var
from ..ontology import get_ontology
from ..security.auth import Principal
from ..security.ratelimit import BusyError, ConcurrencyGate
from ..security.sanitize import (InputError, clean_asset_type, clean_country, clean_date, clean_entity,
                                 clean_incident_type, clean_project_status, clean_text)
from ..units import UnitError, convert
from .security import RequestContextMiddleware, keystore, require

logger = logging.getLogger("energygraph.api")
WEB = Path(__file__).resolve().parent.parent / "web"

Name = Annotated[str, PathParam(min_length=1, max_length=120)]
Term = Annotated[str, Query(min_length=1, max_length=120)]
OptTerm = Annotated[Optional[str], Query(max_length=120)]
OptDate = Annotated[Optional[str], Query(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Limit = Annotated[int, Query(ge=1, le=50)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    problems = settings.problems()
    for p in problems:
        logger.warning("configuration problem: %s", p)
    if problems and settings.is_production:
        raise RuntimeError("refusing to start in production with configuration problems: " + "; ".join(problems))
    n = len(keystore())  # parse keys now: a bad ENERGYGRAPH_API_KEYS fails fast
    if settings.auth_disabled and not settings.is_production:
        logger.warning("authentication is DISABLED (development only); dev entitlements=%s", settings.dev_entitlements)
    logger.info("energygraph api starting: env=%s api_keys=%d oidc=%s", settings.env, n, bool(settings.oidc_issuer))
    yield
    close_read_client()


app = FastAPI(title="EnergyGraph", version="1.0.0", lifespan=lifespan,
              description="Energy knowledge graph + GraphRAG. Not investment, trading or operational advice.",
              docs_url=None if settings.is_production else "/docs", redoc_url=None,
              openapi_url=None if settings.is_production else "/openapi.json")
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "X-API-Key", "Content-Type"])
app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_body_bytes)
app.mount("/static", StaticFiles(directory=WEB), name="static")

_agent = None
_chat_gate = ConcurrencyGate(settings.agent_max_concurrency)


def _error(status: int, message: str, headers: dict | None = None, details=None) -> JSONResponse:
    body = {"error": {"status": status, "message": message, "request_id": request_id_var.get()}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def http_error(_r: Request, exc: StarletteHTTPException):
    return _error(exc.status_code, str(exc.detail), headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_r: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e.get("loc", [])), "problem": e.get("msg")} for e in exc.errors()]
    return _error(422, "invalid request", details=details)


@app.exception_handler(InputError)
async def input_error(_r: Request, exc: InputError):
    return _error(422, str(exc))


@app.exception_handler(UnitError)
async def unit_error(_r: Request, exc: UnitError):
    return _error(422, str(exc))


@app.exception_handler(Exception)
async def unhandled(_r: Request, exc: Exception):
    logger.exception("unhandled error")
    metrics.inc("energygraph_unhandled_errors_total")
    return _error(500, "internal error")


def db():
    return get_read_client()


Read = Annotated[Principal, Depends(require("read"))]
Chat = Annotated[Principal, Depends(require("chat"))]
Admin = Annotated[Principal, Depends(require("admin"))]


# --------------------------------------------------------------------------- probes & meta
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readyz():
    return {"status": "ready"} if db().ping() else _error(503, "neo4j not reachable")


@app.get("/metrics", include_in_schema=False)
def prometheus(_: Admin):
    return PlainTextResponse(metrics.render())


@app.get("/api/me")
def me(p: Read):
    return {"name": p.name, "kind": p.kind, "scopes": sorted(p.scopes), "entitlements": p.ents()}


@app.get("/api/stats")
def stats(_: Admin):
    return q.stats(db())


@app.get("/api/sources")
def sources(p: Read):
    return q.list_sources(db(), p)


@app.get("/api/ontology")
def ontology(_: Read):
    onto = get_ontology()
    return {"classes": [{"name": c.name, "key": c.key, "parent": c.parent, "embedded": bool(c.embed_text),
                         "comment": c.comment} for c in onto.classes.values()],
            "relationships": [{"name": r.name, "domain": r.domain, "range": r.range, "comment": r.comment}
                              for r in onto.relationships.values()]}


# --------------------------------------------------------------------------- graph
@app.get("/api/asset/{name}")
def asset(name: Name, p: Read):
    return q.asset_overview(db(), clean_entity(name, field="name"), p.ents())


@app.get("/api/asset/{name}/similar")
def similar(name: Name, p: Read, limit: Limit = 10):
    return q.similar_assets(db(), clean_entity(name, field="name"), p.ents(), limit=limit)


@app.get("/api/incidents")
def incidents(entity: Term, p: Read, start: OptDate = None, end: OptDate = None,
              incident_type: Annotated[Optional[str], Query(max_length=30)] = None, limit: Limit = 25):
    return q.incident_history(db(), clean_entity(entity, field="entity"), p.ents(), clean_date(start, "start"),
                              clean_date(end, "end"), clean_incident_type(incident_type), limit=limit)


@app.get("/api/ownership/{name}")
def ownership(name: Name, p: Read):
    return q.ownership_chain(db(), clean_entity(name, field="name"), p.ents())


@app.get("/api/taxonomy/expand")
def taxonomy_expand(term: Term, p: Read, max_depth: Annotated[int, Query(ge=0, le=10)] = 5):
    return q.taxonomy_expand(db(), clean_entity(term, field="term"), p.ents(), max_depth=max_depth)


@app.get("/api/taxonomy/decode")
def taxonomy_decode(term: Term, p: Read):
    return q.decode_classification(db(), clean_entity(term, field="term"), p.ents())


@app.get("/api/search")
def search(term: Term, p: Read, expand: bool = True, limit: Limit = 10):
    return q.search_documents(db(), clean_entity(term, field="term"), p.ents(), expand=expand, limit=limit)


@app.get("/api/projects")
def projects(p: Read, country: Annotated[Optional[str], Query(pattern=r"^[A-Za-z]{3}$")] = None,
             asset_type: Annotated[Optional[str], Query(max_length=20)] = None, concept: OptTerm = None,
             company: OptTerm = None, status: Annotated[Optional[str], Query(max_length=20)] = None,
             limit: Limit = 25):
    return q.project_pipeline(db(), p.ents(), country=clean_country(country), asset_type=clean_asset_type(asset_type),
                              concept=clean_entity(concept, field="concept") if concept else None,
                              company=clean_entity(company, field="company") if company else None,
                              status=clean_project_status(status), limit=limit)


@app.get("/api/path")
def path(origin: Term, p: Read, destination: OptTerm = None, max_hops: Annotated[int, Query(ge=1, le=10)] = 6):
    return q.supply_path(db(), clean_entity(origin, field="origin"),
                         clean_entity(destination, field="destination") if destination else None,
                         p.ents(), max_hops=max_hops)


@app.get("/api/prices")
def prices(series: Term, p: Read, start: OptDate = None, end: OptDate = None,
           max_points: Annotated[int, Query(ge=1, le=400)] = 120):
    return q.price_series(db(), clean_entity(series, field="series"), p.ents(), clean_date(start, "start"),
                          clean_date(end, "end"), max_points=max_points)


@app.get("/api/convert")
def convert_units(value: Annotated[float, Query(ge=-1e15, le=1e15)], from_unit: Annotated[str, Query(max_length=20)],
                  to_unit: Annotated[str, Query(max_length=20)], _: Read,
                  commodity: Annotated[Optional[str], Query(max_length=40)] = None,
                  api_gravity: Annotated[Optional[float], Query(ge=5, le=80)] = None):
    params = {"api_gravity": api_gravity} if api_gravity is not None else {}
    return convert(value, clean_text(from_unit, 20), clean_text(to_unit, 20),
                   clean_text(commodity, 40) if commodity else None, **params)


@app.get("/api/semantic")
def semantic(text: Annotated[str, Query(min_length=1, max_length=300)], p: Read,
             k: Annotated[int, Query(ge=1, le=20)] = 5):
    try:
        return q.semantic_search(db(), clean_text(text, 300, field="text"), p.ents(), k=k)
    except InputError:
        raise
    except Exception as exc:
        logger.warning("semantic search unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "semantic search is unavailable") from None


# --------------------------------------------------------------------------- assistant
class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
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
    unverified_numbers: list[str]
    duration_ms: int
    request_id: str


def get_agent():
    global _agent
    if _agent is None:
        from ..agent.agent import EnergyGraphAgent

        _agent = EnergyGraphAgent(db())
    return _agent


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, principal: Chat):
    try:
        with _chat_gate:
            result = get_agent().ask(req.question, [t.model_dump() for t in req.history], principal=principal)
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
                        unverified_ids=result.unverified_ids, unverified_numbers=result.unverified_numbers,
                        duration_ms=result.duration_ms, request_id=request_id_var.get())
