"""The long-term memory store.

A vector store of past incidents, keyed by service and symptom. The retrieval
query the agent actually makes is specific and high value: "find past incidents
on THIS service with symptoms like THIS one." The store is scoped to exactly that
query, not a general "remember everything" bucket, which is the discipline the
chapter argues for.

Backed by Postgres for the teaching version (filter by service, rank by symptom
similarity in Python). Swap in a real vector DB and embedding model for
production; the interface does not change.

Staleness is first-class: every memory carries the date and the service version
it applied to. recall() marks a memory stale when its version differs from the
service's current version, because a remediation that worked before a change can
be exactly wrong after it. The agent weighs current telemetry over a stale memory.
"""
from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Json

from .. import db
from .embeddings import Embedder, LocalHashEmbedder, cosine


@dataclass
class Recollection:
    symptom: str
    root_cause: str | None
    remediation: str | None
    service_version: str | None
    occurred_at: str
    similarity: float
    stale: bool


class MemoryStore:
    def __init__(self, embedder: Embedder | None = None):
        self.embedder = embedder or LocalHashEmbedder()

    def remember(
        self,
        conn,
        *,
        workflow_id: str,
        service: str,
        symptom: str,
        root_cause: str | None,
        remediation: str | None,
        service_version: str | None,
    ) -> bool:
        """Store one incident's memory. Idempotent per workflow: ON CONFLICT DO
        NOTHING keyed by workflow_id, so a replayed or re-run investigation does
        not duplicate its memory. Returns True if a new row was written."""
        emb = self.embedder.embed(symptom)
        cur = conn.execute(
            """
            INSERT INTO memory
                (workflow_id, service, symptom, embedding, root_cause,
                 remediation, service_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workflow_id) DO NOTHING
            """,
            (workflow_id, service, symptom, Json(emb), root_cause,
             remediation, service_version),
        )
        return cur.rowcount == 1

    def recall(
        self, service: str, symptom: str, current_version: str | None = None, k: int = 3
    ) -> list[Recollection]:
        """Find past incidents on this service with similar symptoms.

        Filters by service first (the keyed-by-service part), then ranks by
        symptom-embedding cosine similarity. Marks each result stale if it
        applied to a different service version than the current one.
        """
        query_emb = self.embedder.embed(symptom)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT workflow_id, symptom, embedding, root_cause, remediation, "
                "service_version, occurred_at FROM memory WHERE service = %s",
                (service,),
            ).fetchall()
        scored: list[Recollection] = []
        for r in rows:
            sim = cosine(query_emb, r["embedding"])
            stale = current_version is not None and r["service_version"] not in (None, current_version)
            scored.append(
                Recollection(
                    symptom=r["symptom"],
                    root_cause=r["root_cause"],
                    remediation=r["remediation"],
                    service_version=r["service_version"],
                    occurred_at=str(r["occurred_at"]),
                    similarity=round(sim, 3),
                    stale=stale,
                )
            )
        scored.sort(key=lambda x: x.similarity, reverse=True)
        return scored[:k]
