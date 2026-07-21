"""One-time WelcomeHome history import (Module 18a).

    python -m app.scripts.backfill_welcomehome [--dry-run] [--since 2025-01-01]

The sync loop only ever sweeps forward from its cursor; this is how the existing
CRM history gets in. It is an OPS ACTION, deliberately not a loop concern: it is
slow (the export API caps cursor reuse at 3/minute, so a full history walk takes
minutes), it is run once, and it wants a human watching it.

Design points that matter if you change this:

  * IDEMPOTENT AND RESUMABLE. Everything goes through the same ingest seam the
    loop uses, so re-running is safe — prospects re-match by external id, contacts
    upsert, messages dedupe by (source, activity id) in the communications tier.
    Progress is checkpointed under its own `connector_state` key, so an interrupted
    run resumes at the table it was on rather than starting over.
  * IT NEVER PRINTS RECORD CONTENTS. Counts and table names only. This is real
    client health information and an operator's terminal scrollback is not a
    place for it.
  * `--since` BOUNDS PROMOTION REACH, not just the activity window. Every
    historical Start-of-Care prospect creates an ACTIVE client (see the runbook
    warning below), so how far back you reach is a decision with consequences.

RUNBOOK — after the backfill, before trusting the census:

  1. WelcomeHome has NO discharge signal. It knows who started care; it does not
     know who stopped. Every prospect that ever reached Start of Care therefore
     lands as an `active` client, including engagements that ended years ago.
  2. So: open /clients, sort by created date, and discharge the ones that have
     ended. Until you do, the census count and the authorized-hours denominator
     are both overstated.
  3. `--since` is the blunt control for this — bounding to the last 6-12 months
     usually leaves a handful to review instead of hundreds.
  4. Client intake fields (payer, authorized hours) are null on every promoted
     client. The CRM doesn't carry them; the office fills them in.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ..config import settings
from ..db import close_pool, open_pool, tenant_tx
from ..deps import get_machine_tenant_id
from ..services.connectors.ingest import SYNC_RECEIPT, ingest_payload
from ..services.connectors.sync import read_state, write_state
from ..services.connectors.wh_client import WelcomeHomeClient, WelcomeHomeError
from ..services.connectors.wh_map import build_refs, map_activity, map_prospect
from ..services.connectors.wh_runner import SOURCE, _activity_event_id, _collect
from ..services.communications import (
    embed_communication,
    ingest_communication,
    should_embed,
)
from ..services.views.summary import SummaryUnavailable, regenerate_comm_profile

# Its own cursor key, so an interrupted backfill never disturbs the live loop's.
BACKFILL_KEY = "welcomehome_backfill"

log = logging.getLogger("nexus.backfill.welcomehome")


def _progress(label: str, n: int) -> None:
    """Counts only — never record contents."""
    print(f"  {label}: {n}", flush=True)


async def _ingest_communications(tenant_id: str, pending: list[tuple]) -> int:
    """The split history-seed for messages, in three passes so a large import stays
    efficient (bulk-store first, then embed, then summarize) rather than doing an
    embeddings round-trip inline per row:

      1. STRUCTURED — store every message (embed=False), fast, no network. Idempotent
         by (source, activity id), so a resumed run re-finds rows instead of dupes.
      2. BATCHED EMBED — embed only the messages the policy selects (should_embed).
      3. SUMMARY — regenerate the communication profile for each touched lead.

    All three run outside any transaction; a single bad row is logged and skipped."""
    print(f"Storing {len(pending)} messages (structured pass)…", flush=True)
    to_embed: list[tuple[str, str, str]] = []   # (comm_id, body, lead_id)
    touched_leads: set[str] = set()
    stored = 0
    for channel, direction, occurred_at, body, lead_id, external_id in pending:
        try:
            source_event_id = await _activity_event_id(tenant_id, lead_id, external_id)
            comm_id = await ingest_communication(
                tenant_id, channel=channel, direction=direction,
                occurred_at=occurred_at, body=body, entity_type="lead",
                entity_id=lead_id, source=SOURCE, external_id=external_id,
                source_event_id=source_event_id, embed=False,
            )
            if comm_id is None:
                continue
            stored += 1
            touched_leads.add(lead_id)
            if should_embed(channel, body):
                to_embed.append((comm_id, body, lead_id))
        except Exception:  # noqa: BLE001 — one bad message is not a failed import
            log.exception("could not store WelcomeHome message %s", external_id)
    _progress("messages stored", stored)

    print(f"Embedding {len(to_embed)} messages (batched pass)…", flush=True)
    embedded = 0
    for comm_id, body, lead_id in to_embed:
        try:
            await embed_communication(
                tenant_id, comm_id, body=body, entity_type="lead",
                entity_id=lead_id, source=SOURCE,
            )
            embedded += 1
        except Exception:  # noqa: BLE001
            log.exception("could not embed WelcomeHome message %s", comm_id)
    _progress("messages embedded", embedded)

    print(f"Building communication profiles for {len(touched_leads)} leads…", flush=True)
    profiled = 0
    for lead_id in sorted(touched_leads):
        try:
            async with tenant_tx(tenant_id) as conn:
                await regenerate_comm_profile(
                    conn, tenant_id, entity_type="lead", entity_id=lead_id,
                )
            profiled += 1
        except SummaryUnavailable:
            # No Anthropic key — profiles are optional derived knowledge; the
            # messages are stored and retrievable regardless.
            log.info("skipping comm profiles — no Anthropic key configured")
            break
        except Exception:  # noqa: BLE001
            log.exception("could not build comm profile for lead %s", lead_id)
    _progress("comm profiles built", profiled)

    return stored


async def _run(dry_run: bool, since: str | None) -> int:
    tenant_id = get_machine_tenant_id()
    counts = {"prospects": 0, "activities": 0, "skipped": 0, "messages": 0}
    # (channel, direction, occurred_at, body, lead_id, activity_id) collected during
    # the activity pass, ingested into the communications tier afterwards.
    communications: list[tuple] = []

    async with WelcomeHomeClient() as client:
        print("Fetching reference vocabularies…", flush=True)
        refs = build_refs(
            stages=await client.reference("stages"),
            activity_types=await client.reference("activity_types"),
            lead_sources=await client.reference("lead_sources"),
        )

        print("Reading residents and influencers…", flush=True)
        residents = await _collect(client, "Residents", "residents.prospect_id")
        influencers = await _collect(client, "Influencers", "influencers.prospect_id")
        _progress("prospects with residents", len(residents))
        _progress("prospects with influencers", len(influencers))

        async with tenant_tx(tenant_id) as conn:
            state = await read_state(conn, BACKFILL_KEY)
        done = set(state.get("completed_tables") or [])

        # --- prospects ---------------------------------------------------
        if "Prospects" in done:
            print("Prospects already imported — skipping (resume).", flush=True)
        else:
            print("Importing prospects…", flush=True)
            async for page in client.export_pages("Prospects", since):
                async with tenant_tx(tenant_id) as conn:
                    for row in page:
                        prospect_id = (row.get("prospects.id") or "").strip()
                        mapped = map_prospect(
                            row, refs,
                            residents.get(prospect_id, []),
                            influencers.get(prospect_id, []),
                        )
                        if mapped is None:
                            counts["skipped"] += 1
                            continue
                        if not dry_run:
                            await ingest_payload(
                                SOURCE,
                                {"event": "prospect.synced", "prospect": mapped},
                                tenant_id=tenant_id,
                                receipt_event_type=SYNC_RECEIPT,
                                conn=conn,
                            )
                        counts["prospects"] += 1
                _progress("prospects", counts["prospects"])
            if not dry_run:
                done.add("Prospects")
                async with tenant_tx(tenant_id) as conn:
                    await write_state(
                        conn, tenant_id, BACKFILL_KEY, {"completed_tables": sorted(done)}
                    )

        # --- activities ---------------------------------------------------
        if "Activities" in done:
            print("Activities already imported — skipping (resume).", flush=True)
        else:
            print("Importing activities…", flush=True)
            async for page in client.export_pages("Activities", since):
                async with tenant_tx(tenant_id) as conn:
                    for row in page:
                        mapped = map_activity(row, refs)
                        if mapped is None:
                            counts["skipped"] += 1
                            continue
                        if dry_run:
                            counts["activities"] += 1
                            continue
                        result = await ingest_payload(
                            SOURCE,
                            {"event": "activity.synced", "activity": mapped},
                            tenant_id=tenant_id,
                            receipt_event_type=SYNC_RECEIPT,
                            conn=conn,
                        )
                        counts["activities"] += 1
                        comm = mapped.get("communication")
                        if comm and result.get("matched"):
                            lead = await (
                                await conn.execute(
                                    "select entity_id from public.external_ids "
                                    "where entity_type = 'lead' and external_id = %s",
                                    (mapped["external_id"],),
                                )
                            ).fetchone()
                            if lead:
                                communications.append((
                                    comm["channel"],
                                    comm["direction"],
                                    comm["occurred_at"],
                                    comm["body"],
                                    str(lead[0]),
                                    mapped["activity_id"],
                                ))
                _progress("activities", counts["activities"])
            if not dry_run:
                done.add("Activities")
                async with tenant_tx(tenant_id) as conn:
                    await write_state(
                        conn, tenant_id, BACKFILL_KEY, {"completed_tables": sorted(done)}
                    )

    # --- messages -> communications tier, outside any transaction (embeddings are
    # network calls). See _ingest_communications for the structured / batched-embed
    # / summary pass split.
    if communications and not dry_run:
        counts["messages"] = await _ingest_communications(tenant_id, communications)

    print("\nDone." if not dry_run else "\nDone (dry run — nothing written).")
    for key, value in counts.items():
        _progress(key, value)
    print(
        "\nNEXT STEP — WelcomeHome has no discharge signal, so every historical\n"
        "Start-of-Care prospect was imported as an ACTIVE client. Open /clients and\n"
        "discharge the engagements that have ended before trusting the census.",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill WelcomeHome history.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count what would be imported without writing anything.",
    )
    parser.add_argument(
        "--since", default=None,
        help="ISO date bound (e.g. 2025-01-01). Limits BOTH the activity window "
             "and how far back Start-of-Care promotion reaches. Defaults to "
             "WELCOMEHOME_BACKFILL_SINCE.",
    )
    args = parser.parse_args(argv)
    since = args.since or settings.welcomehome_backfill_since or None

    if not settings.welcomehome_api_key:
        print("WELCOMEHOME_API_KEY is not configured — nothing to do.", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print(f"WelcomeHome backfill — since={since or 'the beginning'}"
          f"{' (dry run)' if args.dry_run else ''}", flush=True)

    async def go() -> int:
        await open_pool()
        try:
            return await _run(args.dry_run, since)
        except WelcomeHomeError as exc:
            print(f"WelcomeHome API error: {exc}", file=sys.stderr)
            return 2
        finally:
            await close_pool()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
