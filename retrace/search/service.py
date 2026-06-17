"""Search: FTS5 full-text, fully-local semantic (NL embeddings), and hybrid.

Embeddings are float32 vectors stored per capture; semantic search is brute-force
cosine in NumPy (fine at single-user scale). Hybrid blends normalized FTS rank
with cosine similarity.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
from sqlalchemy import select, text as sqltext

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Capture, CaptureEmbedding
from ..native.helpers import embed_text as _embed_helper
from ..api.serializers import capture_brief

log = logging.getLogger("retrace.search")


# --- embedding storage ------------------------------------------------------

def _to_vec(payload: dict | None) -> tuple[np.ndarray, str] | None:
    if not payload or not payload.get("ok"):
        return None
    vec = np.asarray(payload.get("vec") or [], dtype=np.float32)
    if vec.size == 0:
        return None
    return vec, str(payload.get("model") or "nl")


def embed_query(text: str, settings: Settings | None = None) -> np.ndarray | None:
    res = _to_vec(_embed_helper(text, settings=settings))
    return res[0] if res else None


def store_capture_embedding(session, capture_id: int, text: str,
                            settings: Settings | None = None) -> bool:
    """Compute + persist an embedding for a capture. Returns True on success."""
    res = _to_vec(_embed_helper(text, settings=settings))
    if res is None:
        return False
    vec, model = res
    session.merge(CaptureEmbedding(
        capture_id=capture_id, dim=int(vec.size), model=model, vec=vec.tobytes()
    ))
    return True


def backfill_embeddings(limit: int = 500, settings: Settings | None = None) -> dict:
    """Embed stored captures that don't yet have an embedding."""
    s = settings or get_settings()
    done = 0
    with session_scope(s) as session:
        rows = session.execute(
            select(Capture.id, Capture.text)
            .outerjoin(CaptureEmbedding, CaptureEmbedding.capture_id == Capture.id)
            .where(CaptureEmbedding.capture_id.is_(None), Capture.text != "")
            .limit(limit)
        ).all()
        for cid, txt in rows:
            if store_capture_embedding(session, cid, txt, s):
                done += 1
    return {"embedded": done, "scanned": len(rows)}


# --- helpers ----------------------------------------------------------------

def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


def _fts_match(q: str) -> str | None:
    import re

    tokens = re.findall(r"\w+", q.lower())
    if not tokens:
        return None
    return " ".join(f"{t}*" for t in tokens)  # prefix AND across tokens


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _filter_clauses(start, end, app):
    clauses, params = [], {}
    if start:
        clauses.append("c.captured_at >= :start"); params["start"] = start
    if end:
        clauses.append("c.captured_at < :end"); params["end"] = end
    if app:
        clauses.append("c.app_name = :app"); params["app"] = app
    return clauses, params


# --- search modes -----------------------------------------------------------

def text_search(q, start=None, end=None, app=None, limit=50, settings=None) -> dict[int, float]:
    """Return {capture_id: normalized_score} via FTS5 bm25."""
    s = settings or get_settings()
    match = _fts_match(q)
    if not match:
        return {}
    clauses, params = _filter_clauses(start, end, app)
    where_extra = (" AND " + " AND ".join(clauses)) if clauses else ""
    params.update({"q": match, "limit": limit})
    sql = sqltext(
        f"""
        SELECT c.id AS id, bm25(captures_fts) AS score
        FROM captures_fts JOIN captures c ON c.id = captures_fts.rowid
        WHERE captures_fts MATCH :q{where_extra}
        ORDER BY score LIMIT :limit
        """
    )
    out: dict[int, float] = {}
    with session_scope(s) as session:
        for row in session.execute(sql, params):
            out[int(row.id)] = 1.0 / (1.0 + max(0.0, float(row.score)))  # lower bm25 -> higher
    return out


def semantic_search(q, start=None, end=None, app=None, limit=50, settings=None) -> dict[int, float]:
    """Return {capture_id: cosine} via brute-force cosine over stored vectors."""
    s = settings or get_settings()
    qv = embed_query(q, s)
    if qv is None:
        return {}
    qn = _normalize(qv)

    stmt = (
        select(CaptureEmbedding.capture_id, CaptureEmbedding.vec, CaptureEmbedding.dim)
        .join(Capture, Capture.id == CaptureEmbedding.capture_id)
    )
    if start:
        stmt = stmt.where(Capture.captured_at >= start)
    if end:
        stmt = stmt.where(Capture.captured_at < end)
    if app:
        stmt = stmt.where(Capture.app_name == app)

    scores: dict[int, float] = {}
    with session_scope(s) as session:
        for cid, blob, dim in session.execute(stmt):
            v = np.frombuffer(blob, dtype=np.float32)
            if v.size != qn.size:  # only compare same-model dims
                continue
            scores[int(cid)] = float(np.dot(qn, _normalize(v)))
    return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit])


def search(q, mode="hybrid", start=None, end=None, app=None, limit=50, settings=None) -> dict:
    s = settings or get_settings()
    start_dt, end_dt = _parse_dt(start), _parse_dt(end)
    q = (q or "").strip()
    if not q:
        return {"mode": mode, "query": q, "results": []}

    wide = max(limit * 3, limit)
    text_scores = text_search(q, start_dt, end_dt, app, wide, s) if mode in ("text", "hybrid") else {}
    sem_scores = semantic_search(q, start_dt, end_dt, app, wide, s) if mode in ("semantic", "hybrid") else {}

    if mode == "text":
        combined = text_scores
    elif mode == "semantic":
        combined = sem_scores
    else:
        ids = set(text_scores) | set(sem_scores)
        combined = {
            i: 0.5 * text_scores.get(i, 0.0) + 0.5 * max(0.0, sem_scores.get(i, 0.0))
            for i in ids
        }

    ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    if not ranked:
        return {"mode": mode, "query": q, "results": []}

    ids = [i for i, _ in ranked]
    with session_scope(s) as session:
        rows = {c.id: c for c in session.execute(
            select(Capture).where(Capture.id.in_(ids))
        ).scalars()}
        results = []
        for cid, score in ranked:
            row = rows.get(cid)
            if row is None:
                continue
            brief = capture_brief(row)
            brief["score"] = round(float(score), 4)
            results.append(brief)
    return {"mode": mode, "query": q, "results": results, "count": len(results)}
