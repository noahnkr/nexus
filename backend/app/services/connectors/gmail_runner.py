"""Gmail sync runner (v1.3.0) — the poll that makes email flow in.

Polling, not Pub/Sub push (user-locked): no public URL and no GCP topic to
maintain, and the fetch-back code is identical to what a push handler would call,
so adding push later is additive rather than a rewrite.

ONE SWEEP:

    history.list(from cursor)  ->  added message ids
    messages.get(each)         ->  full message
    gm_map.map_message         ->  the decoded shape
    ingest_payload("gmail")    ->  receipt -> resolve by email -> email.received

NO BACKFILL, deliberately. A first-ever run stores the mailbox's CURRENT
`historyId` and imports nothing. Gmail's history API cannot reach back more than
about a week anyway, but the real reason is scope: this system mirrors a business
going forward, it does not re-platform years of a mailbox. The office's archive
stays in Gmail, where it is already searchable.

THE CURSOR IS ADVANCED EVEN WHEN A MESSAGE FAILS. A message that cannot be
fetched or mapped is logged and skipped rather than retried forever — one
malformed message must not wedge the mailbox behind it. This is the opposite
choice from the WelcomeHome runner's overlapping watermarks, and deliberately so:
Gmail's history ids are exact and monotonic, so there is no window to re-cover,
and a poison message would otherwise block every email after it indefinitely.

EMAIL BODIES GO TO THE COMMUNICATIONS TIER, attachments go to documents. That
split is CLAUDE.md's knowledge-tier rule, not an implementation detail: an email
someone wrote is correspondence, a PDF they attached is a file. Both happen in
`after_commit`, outside the runner's transaction, because both make network calls
(embeddings, attachment download) and holding a pooled connection across those is
how a sync loop becomes a connection-pool outage.
"""
from __future__ import annotations

import logging

from ...config import settings
from . import gm_map
from .google_client import GoogleClient, HistoryGone, credentials_configured
from .ingest import SYNC_RECEIPT, ingest_payload
from .sync import register_runner

log = logging.getLogger("nexus.connectors.gmail")

SOURCE = "gmail"

# How many messages one cycle will fetch. A sweep that finds more leaves the rest
# for the next cycle (the cursor only advances past what was handled), so a busy
# morning drains over several cycles instead of one very long one holding a
# transaction open.
_MAX_MESSAGES_PER_CYCLE = 50

# MIME types worth parsing into the knowledge corpus. Everything else — images,
# calendar invites, archives, video — is skipped: the corpus is for documents that
# answer questions, and an inbox is an uncontrolled input.
_INGESTIBLE_MIME = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/markdown",
    "text/html",
)


class GmailRunner:
    """SyncRunner for Gmail. Registered at import; inert without credentials."""

    source = SOURCE

    def __init__(self, client_factory=GoogleClient) -> None:
        self._client_factory = client_factory
        # Work discovered by a sweep and performed after its transaction commits:
        # (decoded_message, entity_type, entity_id) per ingested email.
        self._pending: list[tuple] = []

    def enabled(self) -> bool:
        return credentials_configured()

    async def run(self, conn, tenant_id: str, state: dict) -> dict | None:
        new_state = dict(state)
        pending: list[tuple] = []

        async with self._client_factory() as google:
            cursor = new_state.get("history_id")
            if not cursor:
                # First ever run: adopt the mailbox's current position and import
                # nothing. See the module docstring on why there is no backfill.
                profile = await google.gmail_profile()
                history_id = str(profile.get("historyId") or "")
                if not history_id:
                    log.warning("gmail profile carried no historyId; nothing to start from")
                    return None
                log.info("gmail cursor bootstrapped at %s (no backfill)", history_id)
                return {**new_state, "history_id": history_id}

            try:
                message_ids, latest = await self._collect_ids(google, str(cursor))
            except HistoryGone:
                # The cursor aged out (Gmail keeps roughly a week). The gap is
                # unrecoverable by design; re-bootstrap rather than stall forever.
                profile = await google.gmail_profile()
                history_id = str(profile.get("historyId") or "")
                log.warning(
                    "gmail history cursor expired; re-bootstrapping at %s "
                    "(mail in the gap is not imported)", history_id
                )
                return {**new_state, "history_id": history_id or cursor}

            if not message_ids:
                # Nothing new. Still advance to the reported head so the cursor
                # keeps moving on a quiet mailbox and cannot age out.
                return {**new_state, "history_id": latest or cursor} if latest else None

            for message_id in message_ids:
                decoded = await self._fetch_and_map(google, message_id)
                if decoded is None:
                    continue
                outcome = await ingest_payload(
                    SOURCE,
                    {"messages": [decoded], "historyId": latest},
                    tenant_id=tenant_id,
                    receipt_event_type=SYNC_RECEIPT,
                    conn=conn,
                )
                pending.append((decoded, outcome))

            new_state["history_id"] = latest or cursor

        log.info("gmail sweep: %d message(s) ingested", len(pending))
        self._pending = pending
        return new_state

    # -- fetching ----------------------------------------------------------
    async def _collect_ids(self, google, cursor: str) -> tuple[list[str], str | None]:
        """Every added message id since the cursor, plus the new head id."""
        ids: list[str] = []
        latest: str | None = None
        page_token: str | None = None

        while True:
            page = await google.gmail_history(cursor, page_token=page_token)
            latest = str(page.get("historyId") or "") or latest
            for message_id in gm_map.added_message_ids(page):
                if message_id not in ids:
                    ids.append(message_id)
            page_token = page.get("nextPageToken")
            if not page_token or len(ids) >= _MAX_MESSAGES_PER_CYCLE:
                break

        return ids[:_MAX_MESSAGES_PER_CYCLE], latest

    async def _fetch_and_map(self, google, message_id: str) -> dict | None:
        """One message, decoded. None when it should not be ingested.

        Failures are swallowed per message on purpose — see the module docstring
        on why a poison message must not wedge the mailbox behind it.
        """
        try:
            message = await google.gmail_message(message_id)
        except Exception:  # noqa: BLE001 — one bad message must not stop the sweep
            log.exception("gmail could not fetch message %s; skipping", message_id)
            return None
        try:
            return gm_map.map_message(message)
        except Exception:  # noqa: BLE001
            log.exception("gmail could not map message %s; skipping", message_id)
            return None

    # -- after the transaction commits --------------------------------------
    async def after_commit(self, tenant_id: str) -> int:
        """Store email bodies as communications and ingestible attachments as
        documents, now the timeline events they link to have landed.

        Both make network calls (embeddings, attachment download), which is why
        neither happens inside `run`'s transaction.
        """
        pending, self._pending = self._pending, []
        if not pending:
            return 0

        from ..communications import ingest_communication

        stored = 0
        for decoded, _outcome in pending:
            entity_type, entity_id = await _resolved_entity(
                tenant_id, decoded.get("counterpart") or ""
            )
            try:
                await ingest_communication(
                    tenant_id,
                    channel="email",
                    direction=decoded.get("direction"),
                    occurred_at=decoded.get("occurred_at"),
                    body=decoded.get("body") or "",
                    subject=decoded.get("subject"),
                    entity_type=entity_type,
                    entity_id=entity_id,
                    source=SOURCE,
                    external_id=decoded.get("message_id"),
                )
                stored += 1
            except Exception:  # noqa: BLE001 — a bad row must not cost the cursor
                log.exception(
                    "gmail could not store message %s as a communication",
                    decoded.get("message_id"),
                )

            if entity_id:
                await self._ingest_attachments(tenant_id, decoded, entity_type, entity_id)

        return stored

    async def _ingest_attachments(
        self, tenant_id: str, decoded: dict, entity_type: str | None, entity_id: str
    ) -> None:
        """Download and ingest the attachments of an ATTRIBUTED message.

        Only attributed senders' attachments are ingested (the plan's
        ingest-what-you-can-attribute rule). An attachment from someone we cannot
        identify has nowhere meaningful to sit in the corpus, and the review task
        that resolution already raised is the surface for dealing with it.
        """
        max_bytes = settings.gmail_attachment_max_mb * 1024 * 1024
        message_id = decoded.get("message_id")

        for attachment in decoded.get("attachments") or []:
            if attachment.get("mime_type") not in _INGESTIBLE_MIME:
                continue
            if attachment.get("size", 0) > max_bytes:
                log.info(
                    "gmail skipped attachment %s (%s bytes exceeds the %s MB limit)",
                    attachment.get("filename"), attachment.get("size"),
                    settings.gmail_attachment_max_mb,
                )
                continue
            try:
                async with self._client_factory() as google:
                    blob = await google.gmail_attachment(
                        str(message_id), attachment["attachment_id"]
                    )
                data = gm_map.b64url(blob.get("data"))
                if not data:
                    continue
                await _ingest_document(
                    tenant_id, attachment["filename"], data, entity_type, entity_id,
                    mime_type=attachment.get("mime_type"),
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "gmail could not ingest attachment %s", attachment.get("filename")
                )


async def _resolved_entity(tenant_id: str, address: str) -> tuple[str | None, str | None]:
    """Who this address resolved to, read back from `external_ids`.

    Resolution happens inside `ingest_payload`, which returns per-outcome counts
    rather than the entities it landed on. Reading the mapping back is a cheap
    indexed lookup and keeps the core ingest seam's return shape untouched —
    widening it would ripple through every connector's tests for one caller's
    convenience. When resolution raised a review task instead of matching, there
    is no mapping and this correctly returns `(None, None)`: the email is still
    stored, just tenant-general rather than attributed.
    """
    from ...db import tenant_tx

    async with tenant_tx(tenant_id) as conn:
        row = await (
            await conn.execute(
                "select entity_type, entity_id from public.external_ids "
                "where source_system = 'email' and external_id = %s limit 1",
                (address,),
            )
        ).fetchone()
    if row is None:
        return None, None
    return str(row[0]), str(row[1])


async def _ingest_document(
    tenant_id: str, filename: str, data: bytes, entity_type: str | None,
    entity_id: str, mime_type: str | None = None,
) -> None:
    """Create the `documents` row and run it through the ingestion pipeline.

    The same two steps the upload route performs, because an emailed attachment
    is an upload that arrived by a different door — it must land in the corpus
    identically, entity-tagged so retrieval can scope to "this client's files".
    """
    from ...db import tenant_tx
    from ..ingestion import process_document

    async with tenant_tx(tenant_id) as conn:
        row = await (
            await conn.execute(
                """insert into public.documents
                     (tenant_id, filename, mime_type, status, entity_type, entity_id)
                   values (%s, %s, %s, 'uploaded', %s, %s)
                   returning id""",
                (tenant_id, filename, mime_type, entity_type, entity_id),
            )
        ).fetchone()
    document_id = str(row[0])

    await process_document(
        document_id,
        tenant_id,
        filename,
        data,
        entity_type=entity_type,
        entity_id=entity_id,
    )


register_runner(GmailRunner())
