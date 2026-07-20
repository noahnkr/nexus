"""WelcomeHome mapping (Module 18a, Task 5). Pure functions, offline fixtures.

The fixtures carry the REAL export column names and the real reference-endpoint
shapes (captured from the live account at build time); only the people and their
details are invented. That matters: nearly every way this mapping can break is a
column name changing, and a fixture with tidied-up names would hide exactly that.
"""
import csv
import json
import pathlib

from app.services.connectors.wh_map import (
    NARRATIVE_MIN_CHARS,
    activity_summary,
    build_refs,
    is_narrative,
    map_activity,
    map_contact,
    map_prospect,
    stage_status,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "wh"


def _csv(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _json(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _refs() -> dict:
    return build_refs(
        stages=_json("stages.json"),
        activity_types=_json("activity_types.json"),
        lead_sources=_json("lead_sources.json"),
    )


def _prospects() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for page in ("prospects_page1.csv", "prospects_page2.csv", "prospects_page3.csv"):
        for row in _csv(page):
            rows[row["prospects.id"]] = row
    return rows


def _people_by_prospect(name: str, key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in _csv(name):
        out.setdefault(row[key], []).append(row)
    return out


def _mapped(prospect_id: str) -> dict:
    residents = _people_by_prospect("residents.csv", "residents.prospect_id")
    influencers = _people_by_prospect("influencers.csv", "influencers.prospect_id")
    return map_prospect(
        _prospects()[prospect_id],
        _refs(),
        residents.get(prospect_id, []),
        influencers.get(prospect_id, []),
    )


# ---------------------------------------------------------------------------
# stage map — every band in the table
# ---------------------------------------------------------------------------
def test_stage_map_covers_every_live_stage():
    refs = _refs()
    by_name = {s["name"]: str(s["id"]) for s in _json("stages.json")}
    assert stage_status(by_name["Inquiry"], refs) == "new"                 # new_lead
    assert stage_status(by_name["Contact Attempted"], refs) == "contacted"  # pos 1
    assert stage_status(by_name["Contact Made"], refs) == "contacted"       # pos 2
    assert stage_status(by_name["Home Visit Scheduled"], refs) == "qualified"   # pos 3
    assert stage_status(by_name["Home Visit Completed"], refs) == "qualified"   # visit
    assert stage_status(by_name["Start of Care"], refs) == "converted"          # move_in


def test_an_unknown_stage_maps_to_nothing_rather_than_guessing():
    """A stage invented after this shipped must leave the lead's status alone."""
    assert stage_status("99999", _refs()) is None
    assert stage_status(None, _refs()) is None


def test_system_type_wins_over_position():
    """A renamed, re-ordered milestone stage still maps — that's the whole point
    of keying on system_type."""
    refs = build_refs(stages=[
        {"id": 1, "name": "Care Begins", "position": 2, "system_type": "move_in"}
    ])
    assert stage_status("1", refs) == "converted"


# ---------------------------------------------------------------------------
# prospects
# ---------------------------------------------------------------------------
def test_prospect_maps_from_the_primary_resident():
    """The prospect is the deal; the RESIDENT is the care recipient, so the lead's
    name and contact details come from them, not from the prospect row."""
    lead = _mapped("9001")

    assert lead["external_id"] == "wh:prospect:9001"
    assert lead["name"] == "Margaret Ellison"
    assert lead["phone"] == "(630) 555-0142"       # cell beats home
    assert lead["email"] == "m.ellison@example.com"
    assert lead["address"] == "412 Rosewood Lane, Naperville, IL"
    assert lead["zip"] == "60540"
    assert lead["background"].startswith("Daughter called after a fall")
    assert lead["status"] == "new"
    assert lead["client_external_id"] == "wh:resident:7101"


def test_lead_source_is_written_verbatim():
    """Module 16 contract: leads.source is the exact-match key joining a lead to
    its referral partner. Any normalization here silently breaks conversion
    metrics, so this asserts the raw string."""
    assert _mapped("9001")["source"] == "A Place For Mom"
    assert _mapped("9003")["source"] == "Hospital"
    assert _mapped("9004")["source"] == "Family and Friend Referral"


def test_influencers_and_extra_residents_become_contacts():
    lead = _mapped("9001")
    contacts = {c["name"]: c for c in lead["contacts"]}
    assert set(contacts) == {"Claire Ellison-Boyd", "Trevor Ellison"}

    claire = contacts["Claire Ellison-Boyd"]
    assert claire["external_id"] == "wh:influencer:6201"
    assert claire["relationship"] == "Daughter"
    assert claire["is_primary"] is True          # point_of_contact
    assert claire["phone"] == "(630) 555-0233"
    assert contacts["Trevor Ellison"]["is_primary"] is False

    # A spouse who is ALSO a care recipient is a contact, labeled 'resident' —
    # WelcomeHome carries no relationship on residents.
    spouse = [c for c in _mapped("9002")["contacts"] if c["name"] == "Vivian Pryce"]
    assert spouse and spouse[0]["relationship"] == "resident"
    assert spouse[0]["external_id"] == "wh:resident:7103"


def test_the_primary_resident_is_not_duplicated_as_a_contact():
    lead = _mapped("9002")
    assert lead["name"] == "Harold Pryce"
    assert "Harold Pryce" not in {c["name"] for c in lead["contacts"]}


def test_start_of_care_maps_to_converted():
    lead = _mapped("9004")
    assert lead["status"] == "converted"
    assert lead["name"] == "Walter Nkemdi"
    assert lead["client_external_id"] == "wh:resident:7105"


def test_a_closed_prospect_is_lost_regardless_of_its_stage():
    """9005 sits in "Contact Made" but was closed — closed beats the stage."""
    lead = _mapped("9005")
    assert lead["status"] == "lost"


def test_an_unmapped_stage_leaves_the_status_unset():
    """9006 sits in a stage the office invented; the mapping refuses to guess and
    update_lead leaves the row's status alone."""
    assert _mapped("9006")["status"] is None


def test_a_prospect_with_no_people_still_produces_a_findable_lead():
    row = _prospects()["9001"]
    lead = map_prospect(row, _refs(), [], [])
    assert lead["name"] == "WelcomeHome prospect 9001"
    assert lead["contacts"] == []
    assert lead["client_external_id"] is None


def test_map_contact_skips_a_nameless_row():
    assert map_contact({"influencers.id": "1"}, kind="influencer") is None


# ---------------------------------------------------------------------------
# activities
# ---------------------------------------------------------------------------
def _activities() -> dict[str, dict]:
    return {row["activities.id"]: row for row in _csv("activities.csv")}


def test_activity_maps_to_a_plain_language_timeline_entry():
    ev = map_activity(_activities()["5301"], _refs())

    assert ev["external_id"] == "wh:prospect:9001"        # lands on the lead
    assert ev["activity_id"] == "wh:activity:5301"        # its own id, for replay
    assert ev["activity_type"] == "Call"
    assert ev["direction"] == "inbound"
    assert ev["occurred_at"] == "2026-06-01T15:10:00.000Z"   # completed_at wins
    assert ev["summary"].startswith("Call (inbound): Intake call transcript.")
    assert "{" not in ev["summary"]


def test_system_activity_types_are_skipped():
    """`Advance Stage` was 539 of 1,365 rows on the live account and we emit
    lead.stage_changed ourselves — importing these would double every stage move."""
    acts = _activities()
    assert map_activity(acts["5306"], _refs()) is None   # Advance Stage
    assert map_activity(acts["5307"], _refs()) is None   # Prospect Added


def test_non_prospect_and_deleted_activities_are_skipped():
    acts = _activities()
    assert map_activity(acts["5308"], _refs()) is None   # record_type Referrer
    assert map_activity(acts["5309"], _refs()) is None   # discarded_at set


def test_not_applicable_direction_is_dropped():
    ev = map_activity(_activities()["5304"], _refs())
    assert ev["direction"] is None
    assert ev["summary"].startswith("Home Visit: Completed the in-home assessment")


# ---------------------------------------------------------------------------
# narrative gate (what reaches RAG)
# ---------------------------------------------------------------------------
def test_long_narrative_activities_are_flagged_for_ingestion():
    acts = _activities()
    assert map_activity(acts["5301"], _refs())["narrative"] is True   # long Call
    assert map_activity(acts["5305"], _refs())["narrative"] is True   # long Note


def test_short_and_non_narrative_activities_are_not_ingested():
    acts = _activities()
    assert map_activity(acts["5302"], _refs())["narrative"] is False  # short Text
    assert map_activity(acts["5303"], _refs())["narrative"] is False  # short Email
    # A long note on a type that never carries prose still isn't a document.
    assert is_narrative("Home Visit", "x" * (NARRATIVE_MIN_CHARS + 10)) is False
    assert is_narrative("Call", "x" * (NARRATIVE_MIN_CHARS - 1)) is False
    assert is_narrative("Call", "x" * NARRATIVE_MIN_CHARS) is True


def test_activity_summary_truncates_and_never_leaks_a_whole_transcript():
    long_line = "A" * 400
    summary = activity_summary("Call", "inbound", long_line)
    assert len(summary) < 150
    assert summary.endswith("…")

    assert activity_summary("Note", None, "") == "Note logged in WelcomeHome"
    assert activity_summary(None, None, None) == "Activity logged in WelcomeHome"
