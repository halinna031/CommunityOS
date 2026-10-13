"""FastAPI app.   Run:  uvicorn backend.main:app --reload      (then open http://localhost:8000)"""
from __future__ import annotations
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config
from .service import Service, ValidationError

app = FastAPI(title="CommunityOS", version="1.0.0",
              description="Community resource intelligence: hybrid retrieval + explainable ranking.")
svc = Service()


class SearchIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    k: int = Field(5, ge=1, le=10)
    region_id: str = config.DEFAULT_REGION


class ReportIn(BaseModel):
    resource_id: str
    reason: str
    note: str = Field("", max_length=300)
    client_id: str = Field("", max_length=64)


class ResolveIn(BaseModel):
    resource_id: str
    action: str
    updates: dict | None = None


class CandidateResolveIn(BaseModel):
    candidate_id: str
    action: str
    updates: dict | None = None


def admin_only(x_admin_token: str = Header("")) -> None:
    if not secrets.compare_digest(x_admin_token, config.ADMIN_TOKEN):
        raise HTTPException(401, "invalid admin token")


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValidationError as e:
        raise HTTPException(400, str(e))


@app.post("/api/search")
def search(body: SearchIn):
    return _guard(svc.search, body.query, lat=body.lat, lng=body.lng, k=body.k, region_id=body.region_id)


@app.post("/api/report")
def report(body: ReportIn):
    return _guard(svc.report, body.resource_id, body.reason, body.note, body.client_id)


@app.get("/api/meta")
def meta():
    return svc.meta()


@app.get("/api/admin/reports", dependencies=[Depends(admin_only)])
def admin_reports():
    return svc.admin_reports()


@app.post("/api/admin/resolve", dependencies=[Depends(admin_only)])
def admin_resolve(body: ResolveIn):
    return _guard(svc.admin_resolve, body.resource_id, body.action, body.updates)


@app.get("/api/admin/candidates", dependencies=[Depends(admin_only)])
def admin_candidates():
    return svc.admin_candidates()


@app.post("/api/admin/candidates/resolve", dependencies=[Depends(admin_only)])
def admin_resolve_candidate(body: CandidateResolveIn):
    return _guard(svc.admin_resolve_candidate, body.candidate_id, body.action, body.updates)


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(config.FRONTEND_DIR / "admin.html")
