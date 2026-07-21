"""WelcomeHome -> canonical translation (Module 18a). Pure functions, no I/O.

Everything here turns an export CSV row into the compact payload the WelcomeHome
adapter normalizes. Keeping it pure and separate is what makes the mapping
fixture-testable without a network, and what keeps the adapter to its contract
(verify + normalize).

Two things are worth reading before changing anything:

STAGE MAPPING IS CONFIG, NOT CODE. WelcomeHome stages are per-account and the
office renames them. So the map keys on `system_type` (WelcomeHome's own stable
marker for the milestone stages) and falls back to POSITION BANDS for the ones
that carry `system_type: "none"`. A renamed stage keeps working; a genuinely new
stage maps to nothing and the lead's status is left ALONE with a warning event —
never guessed, never crashed. This stays a translation layer both ways: as of
v1.1.2 the Nexus funnel happens to reflect WelcomeHome's stages one-to-one, but
that is the funnel's shape, not a coupling — a renamed or added WH stage still
translates here rather than reshaping `views/leads.LEAD_STAGES`.

    Inquiry              (new_lead, pos 0)  -> new
    Contact Attempted    (pos 1)            -> contact_attempted
    Contact Made         (pos 2)            -> contacted
    Home Visit Scheduled (pos 3)            -> visit_scheduled
    Home Visit Completed (visit,   pos 4)   -> visit_completed
    Start of Care        (move_in, pos 5)   -> converted   [+ client promotion]
    discarded / closed                      -> lost

ACTIVITY TREATMENT was settled empirically against the live account (2026-07-20,
~1,365 activities), not from the docs — the docs' `/activity_types` list turned
out to be incomplete in a way that matters:

  * SYSTEM types (`Advance Stage`, `Prospect Added`, `Score Changed`, `Prospect
    Merged/Closed/Reopened`, `Referrer Closed`) do NOT appear in `/activity_types`
    at all — they are WelcomeHome's own bookkeeping, and they are the BULK of the
    table (`Advance Stage` alone was 539 of 1,365 rows). They are skipped. We emit
    `lead.stage_changed` ourselves from the prospect's stage, so importing these
    would double every stage move in the timeline with a worse summary.
  * MESSAGE/INTERACTION types (`Call`, `Email`, `Text`, `Note`, `Assessment`,
    `Other`) are correspondence. As of v1.1.0 EVERY one is stored in the
    communications tier (store-all) via the `communication` sub-payload — a call
    transcript, a text, a care narrative alike — keyed to a channel by
    `ACTIVITY_CHANNELS`. The embed-selectively policy
    (`communications.should_embed`) decides which get chunked/embedded; there is no
    length gate here anymore. This replaces the pre-v1.1.0 path that routed only
    long narratives into the `documents` corpus.
  * Everything else (`Home Visit`, `Appointment`, …) has no channel and lands on
    the lead timeline only.
"""
from __future__ import annotations

import re

# WelcomeHome's own bookkeeping rows. Not in /activity_types; skipped wholesale.
SYSTEM_ACTIVITY_TYPES = frozenset({
    "Advance Stage",
    "Prospect Added",
    "Prospect Merged",
    "Prospect Closed",
    "Prospect Reopened",
    "Referrer Closed",
    "Score Changed",
})

# WelcomeHome activity type -> communications channel. Every message/interaction
# activity is STORED as a communication (store-all, v1.1.0); the embed-selectively
# policy (communications.should_embed) decides which are chunked/embedded, so the
# old length gate no longer lives here. Types absent from this map (Home Visit,
# Appointment, …) land on the lead timeline only — they are not correspondence.
ACTIVITY_CHANNELS = {
    "Call": "call",
    "Email": "email",
    "Text": "sms",
    "Note": "note",
    "Assessment": "other",
    "Other": "other",
}

# system_type -> Nexus lead status. WelcomeHome's stable milestone markers.
_SYSTEM_TYPE_STATUS = {
    "new_lead": "new",
    "visit": "visit_completed",
    "move_in": "converted",
}

# Fallback for stages carrying system_type "none": position bands. One band per
# stage now that the funnel is one-to-one; anything past the last band is at or
# beyond start of care.
_POSITION_BANDS = (
    (0, "new"),
    (1, "contact_attempted"),
    (2, "contacted"),
    (3, "visit_scheduled"),
    (4, "visit_completed"),
)


def _s(row: dict, key: str) -> str | None:
    """Trimmed non-empty string from a CSV row, else None. Export CSVs use empty
    strings, never NULL, so `''` and "absent" are the same thing here."""
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_true(row: dict, key: str) -> bool:
    return (_s(row, key) or "").lower() == "true"


# ---------------------------------------------------------------------------
# reference vocabularies
# ---------------------------------------------------------------------------
def build_refs(
    stages: list[dict] | None = None,
    activity_types: list[dict] | None = None,
    lead_sources: list[dict] | None = None,
) -> dict:
    """Index the JSON reference endpoints by id. The runner fetches these once per
    cycle and hands the result to every mapping call."""
    return {
        "stages": {str(s.get("id")): s for s in (stages or [])},
        "activity_types": {str(a.get("id")): a for a in (activity_types or [])},
        "lead_sources": {str(s.get("id")): s for s in (lead_sources or [])},
    }


def stage_status(stage_id: str | None, refs: dict) -> str | None:
    """Nexus `leads.status` for a WelcomeHome stage id, or None when the stage is
    unknown to us (a stage created after this shipped). None means LEAVE THE STATUS
    ALONE — see the module docstring."""
    if not stage_id:
        return None
    stage = (refs.get("stages") or {}).get(str(stage_id))
    if stage is None:
        return None

    mapped = _SYSTEM_TYPE_STATUS.get(stage.get("system_type"))
    if mapped is not None:
        return mapped

    position = stage.get("position")
    if not isinstance(position, int):
        return None
    for limit, status in _POSITION_BANDS:
        if position <= limit:
            return status
    return "converted"


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------
def _full_name(row: dict) -> str | None:
    parts = [_s(row, "people.first_name"), _s(row, "people.last_name")]
    name = " ".join(p for p in parts if p)
    return name or None


def _best_phone(row: dict) -> str | None:
    """Cell first — home care coordinators reach families on mobiles."""
    for key in ("people.cell_phone", "people.home_phone", "people.work_phone"):
        phone = _s(row, key)
        if phone:
            return phone
    return None


def _address(row: dict) -> str | None:
    line = ", ".join(
        p for p in (
            _s(row, "addresses.line1"),
            _s(row, "addresses.line2"),
            _s(row, "addresses.city"),
        ) if p
    )
    state = _s(row, "addresses.state")
    if line and state:
        return f"{line}, {state}"
    return line or None


def map_contact(row: dict, *, kind: str) -> dict | None:
    """One Influencer or non-primary Resident row -> a `lead_contacts` payload.

    `kind` is "influencer" or "resident"; a resident who isn't the care recipient
    (a spouse also receiving care) is recorded with the relationship 'resident',
    since WelcomeHome carries no relationship label on residents.
    """
    name = _full_name(row)
    if name is None:
        return None
    external_id = _s(row, f"{kind}s.id")
    if external_id is None:
        return None
    return {
        "external_id": f"wh:{kind}:{external_id}",
        "name": name,
        "relationship": (
            _s(row, "relationships.name") if kind == "influencer" else "resident"
        ),
        "phone": _best_phone(row),
        "email": _s(row, "people.email"),
        "is_primary": _is_true(row, "influencers.point_of_contact"),
    }


def _primary_resident(residents: list[dict]) -> dict | None:
    """The care recipient. WelcomeHome flags them `first_resident`; if nothing is
    flagged (bad data), fall back to the first row rather than dropping the lead's
    only source of a name."""
    if not residents:
        return None
    for row in residents:
        if _is_true(row, "residents.first_resident"):
            return row
    return residents[0]


# ---------------------------------------------------------------------------
# prospects
# ---------------------------------------------------------------------------
def map_prospect(
    row: dict,
    refs: dict,
    residents: list[dict] | None = None,
    influencers: list[dict] | None = None,
) -> dict | None:
    """One Prospects export row (+ its people) -> the payload the adapter
    normalizes into a lead event. None when the row has no usable id.

    A WelcomeHome "prospect" is the DEAL; the resident is the care recipient. The
    lead row therefore takes its name and contact details from the primary
    resident, and everyone else becomes a lead contact.
    """
    prospect_id = _s(row, "prospects.id")
    if prospect_id is None:
        return None

    residents = list(residents or [])
    influencers = list(influencers or [])
    primary = _primary_resident(residents)

    # A prospect with no resident yet (an inquiry taken before anyone asked whose
    # care it is) still deserves a lead row — named after the point of contact if
    # there is one, else labeled plainly so the office can find and fix it.
    name = _full_name(primary) if primary else None
    if name is None:
        for row_i in influencers:
            name = _full_name(row_i)
            if name:
                break
    if name is None:
        name = f"WelcomeHome prospect {prospect_id}"

    # Closed/discarded beats the stage: a prospect parked in "Contact Made" that
    # was then closed is lost, not contacted.
    closed = bool(
        _s(row, "prospects.discarded_at")
        or _s(row, "close_reasons.name")
        or (_s(row, "prospects.status") or "").lower() == "closed"
    )
    status = "lost" if closed else stage_status(_s(row, "stages.id"), refs)

    contacts = []
    for res in residents:
        if primary is not None and res is primary:
            continue
        mapped = map_contact(res, kind="resident")
        if mapped:
            contacts.append(mapped)
    for inf in influencers:
        mapped = map_contact(inf, kind="influencer")
        if mapped:
            contacts.append(mapped)

    return {
        "external_id": f"wh:prospect:{prospect_id}",
        "name": name,
        # VERBATIM (Module 16 contract): `leads.source` is the exact-match key that
        # joins a lead to its referral partner. Never normalized, never prefixed.
        "source": _s(row, "lead_sources.name"),
        "phone": _best_phone(primary) if primary else None,
        "email": _s(primary, "people.email") if primary else None,
        "address": _address(primary) if primary else None,
        "zip": _s(primary, "addresses.zip") if primary else None,
        "background": _s(row, "prospects.story"),
        "status": status,
        "stage_name": _s(row, "stages.name"),
        "contacts": contacts,
        # Registered against the CLIENT on Start-of-Care promotion, so later changes
        # to the care recipient resolve to the client rather than re-creating one.
        "client_external_id": (
            f"wh:resident:{_s(primary, 'residents.id')}"
            if primary and _s(primary, "residents.id")
            else None
        ),
    }


# ---------------------------------------------------------------------------
# activities
# ---------------------------------------------------------------------------
def activity_channel(activity_type: str | None) -> str | None:
    """The communications channel this activity type maps to, or None when the
    activity is not correspondence (a home visit, an appointment) — those land on
    the lead timeline only."""
    if not activity_type:
        return None
    return ACTIVITY_CHANNELS.get(activity_type)


def map_activity(row: dict, refs: dict) -> dict | None:
    """One Activities export row -> the payload the adapter normalizes into a
    `lead.activity_logged` event. None for rows that must not reach a timeline:
    system bookkeeping, deleted rows, and anything not about a prospect.

    A message/interaction type (Call, Email, Text, Note, Assessment, Other) also
    carries a `communication` sub-payload: the runner stores EVERY one of these in
    the communications tier (store-all), and `communications.should_embed` decides
    which get embedded. Types that are not correspondence carry `communication`:
    None and stay timeline-only.
    """
    if _s(row, "activities.discarded_at"):
        return None
    # Activities also hang off Referrers (a marketing call to a discharge planner).
    # Those are not lead activity and have no lead to land on.
    if (_s(row, "activities.record_type") or "").lower() != "prospect":
        return None

    prospect_id = _s(row, "activities.record_id")
    activity_id = _s(row, "activities.id")
    if prospect_id is None or activity_id is None:
        return None

    type_name = _s(row, "activity_types.name")
    if type_name in SYSTEM_ACTIVITY_TYPES:
        return None

    notes = _s(row, "activities.notes")
    direction = _s(row, "activities.direction")
    if direction not in ("inbound", "outbound"):
        direction = None  # 'not_applicable' and any unexpected value -> unknown
    occurred_at = (
        _s(row, "activities.completed_at")
        or _s(row, "activities.scheduled_at")
        or _s(row, "activities.created_at")
    )

    channel = activity_channel(type_name)
    communication = None
    if channel is not None and notes:
        communication = {
            "channel": channel,
            "direction": direction,
            "occurred_at": occurred_at,
            "body": notes,
        }

    return {
        "external_id": f"wh:prospect:{prospect_id}",
        "activity_id": f"wh:activity:{activity_id}",
        "activity_type": type_name or "Activity",
        "direction": direction,
        "notes": notes,
        "occurred_at": occurred_at,
        "summary": activity_summary(type_name, direction, notes),
        "communication": communication,
    }


_TAG_RE = re.compile(r"<[^>]*>")
_BLOCK_RE = re.compile(
    r"<\s*(?:br\s*/?|/\s*(?:p|div|li|tr|h[1-6]))\s*>", re.IGNORECASE
)
_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&apos;": "'", "&#39;": "'", "&nbsp;": " ",
}


def _html_to_text(text: str) -> str:
    """Strip markup from a note so the stored summary is readable everywhere it
    surfaces — chat, tasks, traces — not only where the frontend re-derives it.

    WelcomeHome's Email activities are HTML fragments; without this the stored
    one-liner reads "Email (outbound): <b>Come See Us at the…". Deliberately tiny
    and lossy: this feeds a 120-char summary, and the full text is preserved
    verbatim in the event's `detail.notes`. Mirrors `frontend/src/lib/text.ts`.
    """
    if "<" not in text and "&" not in text:
        return text  # the common case: plain text passes through untouched
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return text


def activity_summary(
    activity_type: str | None, direction: str | None, notes: str | None
) -> str:
    """Plain-language one-liner for the lead timeline: "Call (inbound): Intake call
    transcript…". Never raw JSON, never the whole transcript — the Event Log's
    technical detail carries the full notes (CLAUDE.md)."""
    label = activity_type or "Activity"
    if direction:
        label = f"{label} ({direction})"
    notes = _html_to_text(notes) if notes else notes
    first_line = (notes or "").strip().splitlines()[0] if (notes or "").strip() else ""
    if not first_line:
        return f"{label} logged in WelcomeHome"
    if len(first_line) > 120:
        first_line = first_line[:117].rstrip() + "…"
    return f"{label}: {first_line}"
