"""VERTICAL SEAM — senior-care entity read tools + the reporting schema doc.

This file is to Module 2 what `entities_senior_care.sql` is to Module 0: the one
place a new vertical replaces. Core tools (`documents.py`, `reporting.py`) never
reference care concepts; those live here.

Seven read-only tools over the entity schema. Handlers receive the
already-tenant-scoped connection — RLS does all tenant filtering, so no tool
takes `tenant_id` and no SQL ever mentions it. All UUID inputs are validated
before touching SQL, so a bad id is a clean tool error, not a psycopg exception.
"""
from __future__ import annotations

import uuid

from psycopg.rows import dict_row

from .core import ToolDef, ToolInputError, ToolResult, _jsonable
from .registry import register

LEAD_STATUSES = ["new", "contacted", "qualified", "converted", "lost"]
CLIENT_STATUSES = ["active", "paused", "ended"]
SCHEDULE_STATUSES = ["scheduled", "completed", "cancelled", "no_show"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _require_uuid(args: dict, key: str) -> str:
    raw = args.get(key)
    if raw is None or str(raw).strip() == "":
        raise ToolInputError(f"'{key}' is required.")
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        raise ToolInputError(f"'{key}' must be a valid id.")


def _limit(args: dict, default: int, cap: int = 100) -> int:
    try:
        n = int(args.get("limit", default))
    except (ValueError, TypeError):
        n = default
    return max(1, min(n, cap))


def _like(term: str | None) -> str | None:
    if term is None or str(term).strip() == "":
        return None
    return f"%{term}%"


async def _fetch_all(conn, sql: str, params) -> list[dict]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return [_jsonable(dict(r)) for r in await cur.fetchall()]


async def _fetch_one(conn, sql: str, params) -> dict | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
        return _jsonable(dict(row)) if row else None


async def _name_maps(conn) -> tuple[dict, dict]:
    """qualification id->name and region id->name maps for the tenant. Small
    reference tables, fetched whole so resource rows resolve id arrays to names."""
    quals = {
        r["id"]: r["name"]
        for r in await _fetch_all(conn, "select id, name from public.qualifications", ())
    }
    regions = {
        r["id"]: r["name"]
        for r in await _fetch_all(conn, "select id, name from public.regions", ())
    }
    return quals, regions


async def _resolve_name(conn, table: str, name: str) -> str | None:
    row = await _fetch_one(
        conn, f"select id from public.{table} where name ilike %s limit 1", (name,)
    )
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------
async def _list_leads(conn, args: dict) -> ToolResult:
    status = args.get("status")
    region = args.get("region")
    search = _like(args.get("search"))
    limit = _limit(args, 20)
    rows = await _fetch_all(
        conn,
        """select l.id, l.name, l.phone, l.email, l.source, l.status,
                  r.name as region, l.requirements, l.created_at
             from public.leads l
             left join public.regions r on r.id = l.region_id
            where (%(status)s::text is null or l.status = %(status)s)
              and (%(region)s::text is null or r.name ilike %(region)s)
              and (%(search)s::text is null
                   or l.name ilike %(search)s
                   or l.email ilike %(search)s
                   or l.phone ilike %(search)s)
            order by l.created_at desc
            limit %(limit)s""",
        {"status": status, "region": region, "search": search, "limit": limit},
    )
    filt = _describe({"status": status, "region": region, "search": args.get("search")})
    return ToolResult(f"Found {len(rows)} lead(s){filt}.", {"leads": rows, "count": len(rows)})


async def _get_lead(conn, args: dict) -> ToolResult:
    lead_id = _require_uuid(args, "lead_id")
    lead = await _fetch_one(
        conn,
        """select l.*, r.name as region
             from public.leads l
             left join public.regions r on r.id = l.region_id
            where l.id = %s""",
        (lead_id,),
    )
    if lead is None:
        return ToolResult("No lead found with that id.", {"lead": None})
    lead["external_ids"] = await _fetch_all(
        conn,
        """select source_system, external_id, last_synced_at
             from public.external_ids
            where entity_type = 'lead' and entity_id = %s""",
        (lead_id,),
    )
    return ToolResult(f"Lead: {lead['name']} (status {lead['status']}).", {"lead": lead})


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------
async def _list_clients(conn, args: dict) -> ToolResult:
    status = args.get("status")
    search = _like(args.get("search"))
    limit = _limit(args, 20)
    rows = await _fetch_all(
        conn,
        """select c.id, c.name, c.phone, c.email, c.status, c.lead_id,
                  c.requirements, c.created_at
             from public.clients c
            where (%(status)s::text is null or c.status = %(status)s)
              and (%(search)s::text is null
                   or c.name ilike %(search)s
                   or c.email ilike %(search)s
                   or c.phone ilike %(search)s)
            order by c.created_at desc
            limit %(limit)s""",
        {"status": status, "search": search, "limit": limit},
    )
    filt = _describe({"status": status, "search": args.get("search")})
    return ToolResult(
        f"Found {len(rows)} client(s){filt}.", {"clients": rows, "count": len(rows)}
    )


async def _get_client(conn, args: dict) -> ToolResult:
    client_id = _require_uuid(args, "client_id")
    client = await _fetch_one(
        conn, "select * from public.clients where id = %s", (client_id,)
    )
    if client is None:
        return ToolResult("No client found with that id.", {"client": None})
    client["external_ids"] = await _fetch_all(
        conn,
        """select source_system, external_id, last_synced_at
             from public.external_ids
            where entity_type = 'client' and entity_id = %s""",
        (client_id,),
    )
    client["upcoming_schedules"] = await _fetch_all(
        conn,
        """select s.id, s.start_time, s.end_time, s.status, r.name as resource
             from public.schedules s
             join public.resources r on r.id = s.resource_id
            where s.client_id = %s and s.start_time >= now()
            order by s.start_time
            limit 5""",
        (client_id,),
    )
    return ToolResult(
        f"Client: {client['name']} (status {client['status']}).", {"client": client}
    )


# ---------------------------------------------------------------------------
# resources (caregivers)
# ---------------------------------------------------------------------------
async def _list_resources(conn, args: dict) -> ToolResult:
    qual_name = args.get("qualification")
    region_name = args.get("region")
    search = _like(args.get("search"))
    limit = _limit(args, 20)

    qual_id = await _resolve_name(conn, "qualifications", qual_name) if qual_name else None
    region_id = await _resolve_name(conn, "regions", region_name) if region_name else None

    # A named filter that matches no reference row can never match a resource.
    if (qual_name and qual_id is None) or (region_name and region_id is None):
        missing = qual_name if qual_id is None and qual_name else region_name
        return ToolResult(
            f"No resources found (no match for '{missing}').",
            {"resources": [], "count": 0},
        )

    rows = await _fetch_all(
        conn,
        """select id, name, phone, email, qualification_ids, region_ids, availability
             from public.resources
            where (%(qual_id)s::uuid is null or %(qual_id)s::uuid = any(qualification_ids))
              and (%(region_id)s::uuid is null or %(region_id)s::uuid = any(region_ids))
              and (%(search)s::text is null or name ilike %(search)s or email ilike %(search)s)
            order by name
            limit %(limit)s""",
        {"qual_id": qual_id, "region_id": region_id, "search": search, "limit": limit},
    )

    quals, regions = await _name_maps(conn)
    for r in rows:
        r["qualifications"] = [quals[q] for q in r.pop("qualification_ids") if q in quals]
        r["regions"] = [regions[g] for g in r.pop("region_ids") if g in regions]

    filt = _describe({"qualification": qual_name, "region": region_name, "search": args.get("search")})
    return ToolResult(
        f"Found {len(rows)} caregiver(s){filt}.", {"resources": rows, "count": len(rows)}
    )


async def _get_resource_availability(conn, args: dict) -> ToolResult:
    resource_id = _require_uuid(args, "resource_id")
    resource = await _fetch_one(
        conn,
        "select id, name, availability from public.resources where id = %s",
        (resource_id,),
    )
    if resource is None:
        return ToolResult("No caregiver found with that id.", {"resource": None})
    upcoming = await _fetch_all(
        conn,
        """select s.id, s.start_time, s.end_time, s.status, c.name as client
             from public.schedules s
             join public.clients c on c.id = s.client_id
            where s.resource_id = %s and s.status = 'scheduled' and s.start_time >= now()
            order by s.start_time""",
        (resource_id,),
    )
    return ToolResult(
        f"{resource['name']}: {len(upcoming)} upcoming visit(s).",
        {
            "resource": {"id": resource["id"], "name": resource["name"]},
            "availability": resource["availability"],
            "upcoming_schedules": upcoming,
        },
    )


# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------
async def _list_schedules(conn, args: dict) -> ToolResult:
    client_id = _require_uuid(args, "client_id") if args.get("client_id") else None
    resource_id = _require_uuid(args, "resource_id") if args.get("resource_id") else None
    status = args.get("status")
    date_from = args.get("date_from")
    date_to = args.get("date_to")
    limit = _limit(args, 50, cap=200)
    rows = await _fetch_all(
        conn,
        """select s.id, s.start_time, s.end_time, s.status,
                  c.name as client, r.name as resource
             from public.schedules s
             join public.clients c on c.id = s.client_id
             join public.resources r on r.id = s.resource_id
            where (%(client_id)s::uuid is null or s.client_id = %(client_id)s::uuid)
              and (%(resource_id)s::uuid is null or s.resource_id = %(resource_id)s::uuid)
              and (%(status)s::text is null or s.status = %(status)s)
              and (%(date_from)s::timestamptz is null or s.start_time >= %(date_from)s::timestamptz)
              and (%(date_to)s::timestamptz is null or s.start_time <= %(date_to)s::timestamptz)
            order by s.start_time
            limit %(limit)s""",
        {
            "client_id": client_id,
            "resource_id": resource_id,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        },
    )
    filt = _describe({"status": status})
    return ToolResult(
        f"Found {len(rows)} schedule(s){filt}.", {"schedules": rows, "count": len(rows)}
    )


def _describe(filters: dict) -> str:
    parts = [f"{k}={v}" for k, v in filters.items() if v not in (None, "")]
    return f" ({', '.join(parts)})" if parts else ""


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def _obj(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


register(ToolDef(
    name="list_leads",
    description=(
        "List prospective clients (leads), optionally filtered by pipeline status, "
        "region name, or a text search over name/email/phone."
    ),
    input_schema=_obj({
        "status": {"type": "string", "enum": LEAD_STATUSES, "description": "Pipeline status filter."},
        "region": {"type": "string", "description": "Region name, e.g. 'North County'."},
        "search": {"type": "string", "description": "Case-insensitive match on name, email, or phone."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_leads,
))

register(ToolDef(
    name="get_lead",
    description="Get one lead by id, including its region and cross-system external ids.",
    input_schema=_obj({"lead_id": {"type": "string", "description": "The lead's id."}}, ["lead_id"]),
    handler=_get_lead,
))

register(ToolDef(
    name="list_clients",
    description=(
        "List active care recipients (clients), optionally filtered by status or a "
        "text search over name/email/phone."
    ),
    input_schema=_obj({
        "status": {"type": "string", "enum": CLIENT_STATUSES, "description": "Client status filter."},
        "search": {"type": "string", "description": "Case-insensitive match on name, email, or phone."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_clients,
))

register(ToolDef(
    name="get_client",
    description=(
        "Get one client by id, including cross-system external ids and their next "
        "few upcoming scheduled visits."
    ),
    input_schema=_obj({"client_id": {"type": "string", "description": "The client's id."}}, ["client_id"]),
    handler=_get_client,
))

register(ToolDef(
    name="list_resources",
    description=(
        "List caregivers (resources), optionally filtered by a qualification name "
        "(e.g. 'Dementia Care'), a region name, or a text search. Returns each "
        "caregiver's qualification and region names."
    ),
    input_schema=_obj({
        "qualification": {"type": "string", "description": "Qualification name, e.g. 'Dementia Care'."},
        "region": {"type": "string", "description": "Region name the caregiver serves."},
        "search": {"type": "string", "description": "Case-insensitive match on name or email."},
        "limit": {"type": "integer", "default": 20, "maximum": 100},
    }),
    handler=_list_resources,
))

register(ToolDef(
    name="get_resource_availability",
    description=(
        "Get one caregiver's weekly availability plus their upcoming scheduled "
        "visits, by caregiver id."
    ),
    input_schema=_obj({"resource_id": {"type": "string", "description": "The caregiver's id."}}, ["resource_id"]),
    handler=_get_resource_availability,
))

register(ToolDef(
    name="list_schedules",
    description=(
        "List scheduled visits joining client and caregiver names, optionally "
        "filtered by client, caregiver, status, or a start-time date range "
        "(ISO-8601). Ordered by start time."
    ),
    input_schema=_obj({
        "client_id": {"type": "string", "description": "Filter to one client's visits."},
        "resource_id": {"type": "string", "description": "Filter to one caregiver's visits."},
        "status": {"type": "string", "enum": SCHEDULE_STATUSES, "description": "Visit status filter."},
        "date_from": {"type": "string", "description": "Only visits starting on/after this ISO-8601 time."},
        "date_to": {"type": "string", "description": "Only visits starting on/before this ISO-8601 time."},
        "limit": {"type": "integer", "default": 50, "maximum": 200},
    }),
    handler=_list_schedules,
))


# ---------------------------------------------------------------------------
# SQL_SCHEMA_DOC — injected into run_report's description (reporting.py). Concise
# table/column/enum reference for the allowlisted read surface.
# ---------------------------------------------------------------------------
SQL_SCHEMA_DOC = """\
Tables available for read-only reporting (rows are automatically filtered to the
current tenant — never add a tenant_id condition):

leads(id uuid, name text, phone text, email text, source text,
      status text {new|contacted|qualified|converted|lost},
      region_id uuid -> regions.id, requirements jsonb, created_at timestamptz)
clients(id uuid, lead_id uuid -> leads.id, name text, phone text, email text,
        status text {active|paused|ended}, requirements jsonb, created_at timestamptz)
resources(id uuid, name text, phone text, email text, qualification_ids uuid[],
          region_ids uuid[], availability jsonb, created_at timestamptz)  -- caregivers
schedules(id uuid, resource_id uuid -> resources.id, client_id uuid -> clients.id,
          start_time timestamptz, end_time timestamptz,
          status text {scheduled|completed|cancelled|no_show}, created_at timestamptz)
regions(id uuid, name text, zip_codes text[])
qualifications(id uuid, name text, description text)
events(id uuid, source_system text, event_type text, entity_type text,
       entity_id uuid, payload jsonb, created_at timestamptz)  -- immutable audit log
tasks(id uuid, title text, description text, status text, priority text,
      originating_event_id uuid -> events.id, assigned_to text, due_at timestamptz,
      created_at timestamptz, resolved_at timestamptz)
pending_actions(id uuid, task_id uuid -> tasks.id, tool_name text, tool_input jsonb,
                status text {pending|approved|rejected}, created_at timestamptz,
                resolved_at timestamptz)
external_ids(id uuid, entity_type text, entity_id uuid, source_system text,
             external_id text, last_synced_at timestamptz)
documents(id uuid, filename text, mime_type text,
          status text {uploaded|processing|ready|failed}, created_at timestamptz)

resources.qualification_ids / region_ids are uuid arrays: filter with
`<id> = any(qualification_ids)`, or `join qualifications q on q.id = any(r.qualification_ids)`
to get qualification names.
"""
