"""Automation binding (Module 9b, Task 1), gated on NEXUS_APP_DB_URL.

Proves the generic view-binding contract on the automations API: a binding
round-trips on GET/list; the one-sequence-per-(view,stage) unique index surfaces
as a 409 plain message; a different stage is allowed; the ?view= filter scopes the
list; bad binding shapes are 422; PATCH can set and clear a binding; the unique
index is tenant-scoped (the same binding is allowed in another tenant); and a
probe-tenant bound row is invisible through the demo API.

Everything the builder saves is an ordinary automation — a real trigger/steps are
used so validate_recipe passes. Created rows are deleted afterward.
"""
import asyncio
import uuid

import httpx
import pytest

from conftest import DEMO_TENANT, NEXUS_APP_DB_URL, PROBE_TENANT, bearer_headers

pytestmark = pytest.mark.skipif(not NEXUS_APP_DB_URL, reason="NEXUS_APP_DB_URL not set")

# A minimal valid recipe (a safe function step) the binding rides on.
_RECIPE = {
    "trigger": {"type": "event", "event_type": "lead.stage_changed"},
    "conditions": [{"field": "trigger.payload.to", "op": "eq", "value": "contacted"}],
    "steps": [{"type": "function", "function": "now", "save_as": "ts"}],
}


def _body(name, binding):
    return {"name": name, **_RECIPE, "binding": binding}


async def _scenario():
    from app import db
    from app.main import app

    token = uuid.uuid4().hex[:8]
    out = {"token": token, "created_ids": [], "probe_id": None}

    await db.open_pool()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t", headers=bearer_headers()
        ) as ac:
            view = f"leads-{token}"  # unique per run so reruns don't collide

            # --- create with a binding -> round-trips on the response ---
            r1 = await ac.post("/api/automations", json=_body(
                f"seq-contacted {token}", {"view": view, "stage": "contacted"}))
            out["create_code"] = r1.status_code
            a1 = r1.json()
            out["create_binding"] = a1.get("binding")
            out["created_ids"].append(a1["id"])

            # --- GET by id carries the binding ---
            got = (await ac.get(f"/api/automations/{a1['id']}")).json()
            out["get_binding"] = got.get("binding")

            # --- second automation, SAME (view, stage) -> 409 plain message ---
            dup = await ac.post("/api/automations", json=_body(
                f"dup {token}", {"view": view, "stage": "contacted"}))
            out["dup_code"] = dup.status_code
            out["dup_detail"] = dup.json().get("detail")

            # --- different stage, same view -> allowed ---
            r2 = await ac.post("/api/automations", json=_body(
                f"seq-visit-scheduled {token}",
                {"view": view, "stage": "visit_scheduled"}))
            out["diff_stage_code"] = r2.status_code
            if r2.status_code == 201:
                out["created_ids"].append(r2.json()["id"])

            # --- ?view= filters the list to this view's two sequences ---
            listed = (await ac.get(f"/api/automations?view={view}")).json()
            out["view_list_ids"] = sorted(x["id"] for x in listed)
            out["view_list_stages"] = sorted(
                x["binding"]["stage"] for x in listed if x.get("binding")
            )

            # --- bad binding shapes -> 422 ---
            out["not_object_code"] = (await ac.post("/api/automations", json=_body(
                f"bad1 {token}", "nope"))).status_code
            out["missing_view_code"] = (await ac.post("/api/automations", json=_body(
                f"bad2 {token}", {"stage": "contacted"}))).status_code
            out["empty_value_code"] = (await ac.post("/api/automations", json=_body(
                f"bad3 {token}", {"view": ""}))).status_code

            # --- PATCH can set a binding on an unbound automation, then clear it ---
            unbound = await ac.post("/api/automations", json={
                "name": f"unbound {token}", **_RECIPE})
            uid = unbound.json()["id"]
            out["created_ids"].append(uid)
            set_b = await ac.patch(f"/api/automations/{uid}", json={
                "binding": {"view": view, "stage": "new"}})
            out["patch_set_binding"] = set_b.json().get("binding")
            clear_b = await ac.patch(f"/api/automations/{uid}", json={"binding": None})
            out["patch_clear_binding"] = clear_b.json().get("binding")

            # --- unique index is tenant-scoped: same (view, stage) in probe tenant OK ---
            async with db.tenant_tx(PROBE_TENANT) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """insert into public.automations
                             (tenant_id, name, status, trigger, conditions, steps, binding)
                           values (%s, %s, 'paused', '{}', '[]', '[]',
                                   jsonb_build_object('view', %s::text, 'stage', 'contacted'))
                           returning id""",
                        (PROBE_TENANT, f"probe seq {token}", view),
                    )
                    out["probe_id"] = str((await cur.fetchone())[0])
            out["probe_cross_tenant_ok"] = out["probe_id"] is not None

            # --- probe row invisible through the demo API ?view= ---
            demo_view = (await ac.get(f"/api/automations?view={view}")).json()
            out["probe_invisible"] = out["probe_id"] not in {x["id"] for x in demo_view}

        # cleanup
        async with db.tenant_tx(DEMO_TENANT) as conn:
            for aid in out["created_ids"]:
                await conn.execute("delete from public.automations where id=%s", (aid,))
        async with db.tenant_tx(PROBE_TENANT) as conn:
            if out["probe_id"]:
                await conn.execute(
                    "delete from public.automations where id=%s", (out["probe_id"],)
                )
        return out
    finally:
        await db.close_pool()


def test_automation_binding():
    out = asyncio.run(_scenario())

    assert out["create_code"] == 201
    assert out["create_binding"] == {"view": f"leads-{out['token']}", "stage": "contacted"}
    assert out["get_binding"] == out["create_binding"]

    assert out["dup_code"] == 409
    assert "already has a sequence" in str(out["dup_detail"]).lower()

    assert out["diff_stage_code"] == 201
    assert out["view_list_stages"] == ["contacted", "visit_scheduled"]
    assert len(out["view_list_ids"]) == 2

    assert out["not_object_code"] == 422
    assert out["missing_view_code"] == 422
    assert out["empty_value_code"] == 422

    assert out["patch_set_binding"] == {"view": f"leads-{out['token']}", "stage": "new"}
    assert out["patch_clear_binding"] is None

    assert out["probe_cross_tenant_ok"]  # same binding allowed in the other tenant
    assert out["probe_invisible"]  # RLS isolates it through the demo API
