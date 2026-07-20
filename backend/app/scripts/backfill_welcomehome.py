"""One-time WelcomeHome history import (Module 18a).

    python -m app.scripts.backfill_welcomehome [--dry-run] [--since 2025-01-01]

The sync loop only ever sweeps forward from its cursor; this is how the existing
CRM history gets in. It is an OPS ACTION, deliberately not a loop concern: it is
slow (the export API caps cursor reuse at 3/minute, so a full history walk takes
minutes), it is run once, and it wants a human watching it.

Design points that matter if you change this:

  * IDEMPOTENT AND RESUMABLE. Everything goes through the same ingest seam the
    loop uses, so re-running is safe — prospects re-match by external id, contacts
    upsert, transcripts dedupe by activity id. Progress is checkpointed under its
    own `connector_state` key, so an interrupted run resumes at the table it was
    on rather than starting over.
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
from ..services.connectors.wh_runner import SOURCE, _collect
from ..services.ingestion import ingest_text

# Its own cursor key, so an interrupted backfill never disturbs the live loop's.
BACKFILL_KEY = "welcomehome_backfill"

log = logging.getLogger("nexus.backfill.welcomehome")


def _progress(label: str, n: int) -> None:
    """Counts only — never record contents."""
    print(f"  {label}: {n}", flush=True)


async def _run(dry_run: bool, since: str | None) -> int:
    tenant_id = get_machine_tenant_id()
    counts = {"prospects": 0, "activities": 0, "skipped": 0, "transcripts": 0}
    transcripts: list[tuple] = []

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
                        if mapped.get("narrative") and result.get("matched"):
                            lead = await (
                                await conn.execute(
                                    "select entity_id from public.external_ids "
                                    "where entity_type = 'lead' and external_id = %s",
                                    (mapped["external_id"],),
                                )
                            ).fetchone()
                            if lead:
                                transcripts.append((
                                    f"{mapped['activity_type']} — WelcomeHome activity",
                                    mapped.get("notes") or "",
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

    # --- transcripts, outside any transaction (embeddings are network calls) ---
    if transcripts and not dry_run:
        print(f"Ingesting {len(transcripts)} transcripts…", flush=True)
        for title, text, lead_id, external_id in transcripts:
            try:
                await ingest_text(
                    tenant_id, title, text,
                    entity_type="lead", entity_id=lead_id,
                    source=SOURCE, external_id=external_id,
                )
                counts["transcripts"] += 1
            except Exception:  # noqa: BLE001 — one bad transcript is not a failed import
                log.exception("could not ingest transcript %s", external_id)
        _progress("transcripts", counts["transcripts"])

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
