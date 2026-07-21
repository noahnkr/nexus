"""WelcomeHome sync runner (Module 18a) — the SyncRunner the loop drives.

One sweep, in dependency order:

    references (stages / activity_types / lead_sources)   fetched once per cycle
    Residents + Influencers      -> held in memory, keyed by prospect
    Prospects                    -> lead create/update + contacts + promotion
    Activities                   -> lead timeline events (+ messages to comms tier)

Residents and Influencers are read BEFORE prospects and buffered rather than
ingested on their own: a WelcomeHome prospect row carries no name — the care
recipient's name lives on the resident — so a prospect is not mappable until its
people are in hand. They are small (tens to low hundreds of rows for this
account), so buffering costs nothing and saves a second pass.

CURSORS. One watermark per table under `connector_state.state["cursors"]`, each
advanced only after its table's rows are ingested in the runner's transaction.
The window is deliberately re-overlapped by `_CURSOR_OVERLAP_SECONDS`: export
watermarks are server-side timestamps, and re-seeing a handful of rows is free
(ingestion is idempotent by external id) while missing one is silent data loss.

COMMUNICATIONS (v1.1.0). Every message/interaction activity (Call, Email, Text,
Note, Assessment, Other — see `wh_map.ACTIVITY_CHANNELS`) is stored in the
communications tier, entity-tagged to the lead and linked to its
`lead.activity_logged` event as the spine. Store-all: a short text and a long
call transcript alike become communication rows; `communications.should_embed`
decides which are embedded and thus retrievable in chat. This replaced the
pre-v1.1.0 path that routed only long narratives into the `documents` corpus.
The ingestion runs OUTSIDE the runner's transaction — it makes network calls to
the embeddings API, and holding a database transaction open across those is how a
sync loop turns into a connection-pool outage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ...config import settings
from .ingest import SYNC_RECEIPT, ingest_payload
from .sync import register_runner
from .wh_client import WelcomeHomeClient, WelcomeHomeError
from .wh_map import build_refs, map_activity, map_prospect

log = logging.getLogger("nexus.connectors.welcomehome")

SOURCE = "welcomehome"

# Re-read this far back past the stored watermark each sweep. Cheap insurance
# against clock skew and same-second writes; ingestion dedupes by external id.
_CURSOR_OVERLAP_SECONDS = 120

# Per-sweep row caps. A sweep is not a backfill — `scripts/backfill_welcomehome.py`
# is. These keep one cycle bounded so a burst upstream can't monopolize the loop.
_MAX_PROSPECTS = 2000
_MAX_ACTIVITIES = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _watermark(cursor: str | None) -> str | None:
    """The stored cursor pulled back by the overlap window."""
    if not cursor:
        return None
    try:
        parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed - timedelta(seconds=_CURSOR_OVERLAP_SECONDS)).isoformat()


async def _collect(client: WelcomeHomeClient, table: str, key: str) -> dict[str, list[dict]]:
    """Read a whole people table into `{prospect_id: [row, …]}`."""
    out: dict[str, list[dict]] = {}
    async for page in client.export_pages(table):
        for row in page:
            prospect_id = (row.get(key) or "").strip()
            if prospect_id:
                out.setdefault(prospect_id, []).append(row)
    return out


class WelcomeHomeRunner:
    """SyncRunner for WelcomeHome. Registered at import; inert without a key."""

    source = SOURCE

    def __init__(self) -> None:
        # (channel, direction, occurred_at, body, lead_id, external_id) discovered
        # by a sweep, ingested into the communications tier by after_commit once the
        # transaction that found them (and wrote their timeline events) has landed.
        self._pending_communications: list[tuple] = []

    def enabled(self) -> bool:
        return bool(settings.welcomehome_api_key)

    async def run(self, conn, tenant_id: str, state: dict) -> dict:
        cursors = dict(state.get("cursors") or {})
        started = _now_iso()
        pending_communications: list[tuple] = []

        async with WelcomeHomeClient() as client:
            refs = build_refs(
                stages=await client.reference("stages"),
                activity_types=await client.reference("activity_types"),
                lead_sources=await client.reference("lead_sources"),
            )

            residents = await _collect(client, "Residents", "residents.prospect_id")
            influencers = await _collect(client, "Influencers", "influencers.prospect_id")

            prospects_seen = await self._sync_prospects(
                conn, tenant_id, client, refs, residents, influencers,
                _watermark(cursors.get("Prospects")),
            )
            cursors["Prospects"] = started

            activities_seen = await self._sync_activities(
                conn, tenant_id, client, refs, _watermark(cursors.get("Activities")),
                pending_communications,
            )
            cursors["Activities"] = started

        log.info(
            "welcomehome sweep: %s prospects, %s activities, %s messages queued",
            prospects_seen, activities_seen, len(pending_communications),
        )
        self._pending_communications = pending_communications
        return {"cursors": cursors, "last_sweep_at": started}

    async def after_commit(self, tenant_id: str) -> int:
        """Ingest into the communications tier the messages this sweep queued, now
        that its transaction (and the timeline events those messages link to) has
        landed — the embeddings round-trips happen with no transaction held open.
        A message that fails to ingest is logged and skipped: a bad row must not
        cost the sweep its cursor."""
        from ..communications import ingest_communication

        pending, self._pending_communications = self._pending_communications, []
        ingested = 0
        for channel, direction, occurred_at, body, lead_id, external_id in pending:
            try:
                # Event-as-spine: link the communication to the timeline event this
                # activity already wrote (its wh_activity_id is on the event detail).
                source_event_id = await _activity_event_id(tenant_id, lead_id, external_id)
                await ingest_communication(
                    tenant_id,
                    channel=channel,
                    direction=direction,
                    occurred_at=occurred_at,
                    body=body,
                    entity_type="lead",
                    entity_id=lead_id,
                    source=SOURCE,
                    external_id=external_id,
                    source_event_id=source_event_id,
                )
                ingested += 1
            except Exception:  # noqa: BLE001
                log.exception("could not ingest WelcomeHome message %s", external_id)
        return ingested

    # -- prospects ---------------------------------------------------------
    async def _sync_prospects(
        self, conn, tenant_id, client, refs, residents, influencers, watermark
    ) -> int:
        seen = 0
        async for page in client.export_pages("Prospects", watermark):
            for row in page:
                prospect_id = (row.get("prospects.id") or "").strip()
                mapped = map_prospect(
                    row, refs,
                    residents.get(prospect_id, []),
                    influencers.get(prospect_id, []),
                )
                if mapped is None:
                    continue
                await ingest_payload(
                    SOURCE,
                    {"event": "prospect.synced", "prospect": mapped},
                    tenant_id=tenant_id,
                    receipt_event_type=SYNC_RECEIPT,
                    conn=conn,
                )
                seen += 1
                if seen >= _MAX_PROSPECTS:
                    log.warning("welcomehome prospect sweep hit the %s-row cap", _MAX_PROSPECTS)
                    return seen
        return seen

    # -- activities --------------------------------------------------------
    async def _sync_activities(
        self, conn, tenant_id, client, refs, watermark, pending
    ) -> int:
        seen = 0
        async for page in client.export_pages("Activities", watermark):
            for row in page:
                mapped = map_activity(row, refs)
                if mapped is None:
                    continue
                result = await ingest_payload(
                    SOURCE,
                    {"event": "activity.synced", "activity": mapped},
                    tenant_id=tenant_id,
                    receipt_event_type=SYNC_RECEIPT,
                    conn=conn,
                )
                seen += 1
                # Store the message once we know which lead it belongs to — an
                # activity that resolved to a review task has no lead to tag.
                comm = mapped.get("communication")
                if comm and result.get("matched"):
                    lead_id = await _lead_id_for(conn, mapped["external_id"])
                    if lead_id:
                        pending.append((
                            comm["channel"],
                            comm["direction"],
                            comm["occurred_at"],
                            comm["body"],
                            lead_id,
                            mapped["activity_id"],
                        ))
                if seen >= _MAX_ACTIVITIES:
                    log.warning(
                        "welcomehome activity sweep hit the %s-row cap", _MAX_ACTIVITIES
                    )
                    return seen
        return seen


async def _lead_id_for(conn, external_id: str) -> str | None:
    row = await (
        await conn.execute(
            "select entity_id from public.external_ids "
            "where entity_type = 'lead' and external_id = %s",
            (external_id,),
        )
    ).fetchone()
    return str(row[0]) if row else None


async def _activity_event_id(tenant_id: str, lead_id: str, activity_id: str) -> str | None:
    """The `lead.activity_logged` event this activity wrote, for the comm's
    event-as-spine link. Matched on the wh_activity_id the adapter stamps into the
    event detail. Best-effort — a missing event just leaves source_event_id null."""
    from ...db import tenant_tx

    async with tenant_tx(tenant_id) as conn:
        row = await (
            await conn.execute(
                "select id from public.events "
                "where entity_type = 'lead' and entity_id = %s "
                "  and event_type = 'lead.activity_logged' "
                "  and payload->'detail'->>'wh_activity_id' = %s "
                "order by created_at desc limit 1",
                (lead_id, activity_id),
            )
        ).fetchone()
    return str(row[0]) if row else None




register_runner(WelcomeHomeRunner())

__all__ = ["WelcomeHomeRunner", "WelcomeHomeError", "SOURCE"]
