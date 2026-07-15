"""Tenant-isolation tests over the real RLS surface (PostgREST as the
`authenticated` role, driven by locally-minted tenant JWTs)."""
import pytest
from postgrest.exceptions import APIError

A_LEAD = "33333333-0000-0000-0000-000000000001"  # a demo-tenant lead
PROBE_LEAD = "bbbbbbbb-0000-0000-0000-000000000001"  # the probe-tenant lead


def _lead_ids(client):
    return {r["id"] for r in client.table("leads").select("id").execute().data}


def test_tenant_a_sees_its_leads_only(client_tenant_a):
    ids = _lead_ids(client_tenant_a)
    assert A_LEAD in ids
    assert PROBE_LEAD not in ids
    assert len(ids) >= 6  # six demo leads seeded


def test_tenant_b_sees_its_leads_only(client_tenant_b):
    ids = _lead_ids(client_tenant_b)
    assert ids == {PROBE_LEAD}
    assert A_LEAD not in ids


def test_anon_sees_nothing(client_anon):
    assert _lead_ids(client_anon) == set()


def test_tenant_b_cannot_write_as_tenant_a(client_tenant_b, demo_tenant_id):
    """RLS WITH CHECK must reject inserting a row stamped with another tenant."""
    with pytest.raises(APIError):
        client_tenant_b.table("tasks").insert(
            {"tenant_id": demo_tenant_id, "title": "cross-tenant intrusion"}
        ).execute()


def test_tenant_a_sees_its_tenant_row_only(client_tenant_a, demo_tenant_id, probe_tenant_id):
    rows = client_tenant_a.table("tenants").select("id").execute().data
    ids = {r["id"] for r in rows}
    assert ids == {demo_tenant_id}
    assert probe_tenant_id not in ids
