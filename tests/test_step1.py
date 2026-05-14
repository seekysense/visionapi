"""Step 1 — Doppio livello auth + actions CRUD."""
import pytest
from tests.conftest import ADMIN_HDR, BAD_HDR, READER_HDR


# ── autenticazione GET /actions ───────────────────────────────────────────────

def test_get_actions_reader_key(client):
    r = client.get("/actions", headers=READER_HDR)
    assert r.status_code == 200

def test_get_actions_no_key(client):
    r = client.get("/actions")
    assert r.status_code == 401

def test_get_actions_wrong_key(client):
    r = client.get("/actions", headers=BAD_HDR)
    assert r.status_code == 401

def test_get_actions_admin_key_rejected_on_reader_endpoint(client):
    """X-Admin-Key non deve essere accettato dove serve X-API-Key."""
    r = client.get("/actions", headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 401


# ── POST /actions ─────────────────────────────────────────────────────────────

def test_post_action_reader_key_rejected(client):
    payload = {"id": "test_action", "label": "Test", "prompt_single": "test"}
    r = client.post("/actions", json=payload, headers=READER_HDR)
    assert r.status_code == 401

def test_post_action_no_key_rejected(client):
    payload = {"id": "test_action", "label": "Test", "prompt_single": "test"}
    r = client.post("/actions", json=payload)
    assert r.status_code == 401

def test_post_action_admin_key_accepted(client):
    payload = {"id": "step1_test", "label": "Step1 Test", "prompt_single": "test prompt"}
    r = client.post("/actions", json=payload, headers=ADMIN_HDR)
    assert r.status_code in (201, 409)  # 409 se già esiste da run precedente

def test_post_action_returns_correct_fields(client):
    payload = {"id": "step1_fields_test", "label": "Fields Test", "prompt_single": "p"}
    # Rimuove se già esiste
    client.delete("/actions/step1_fields_test", headers=ADMIN_HDR)
    r = client.post("/actions", json=payload, headers=ADMIN_HDR)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "step1_fields_test"
    assert body["label"] == "Fields Test"

def test_post_action_duplicate_returns_409(client):
    payload = {"id": "step1_dup", "label": "Dup", "prompt_single": "p"}
    client.post("/actions", json=payload, headers=ADMIN_HDR)
    r = client.post("/actions", json=payload, headers=ADMIN_HDR)
    assert r.status_code == 409


# ── PUT /actions/{action_id} ──────────────────────────────────────────────────

def test_put_action_admin_key(client):
    # Crea
    client.post(
        "/actions",
        json={"id": "step1_put_test", "label": "Old Label", "prompt_single": "old"},
        headers=ADMIN_HDR,
    )
    # Aggiorna
    r = client.put(
        "/actions/step1_put_test",
        json={"id": "step1_put_test", "label": "New Label", "prompt_single": "new prompt"},
        headers=ADMIN_HDR,
    )
    assert r.status_code == 200
    assert r.json()["label"] == "New Label"

def test_put_action_reader_key_rejected(client):
    r = client.put(
        "/actions/people_count",
        json={"id": "people_count", "label": "X", "prompt_single": "x"},
        headers=READER_HDR,
    )
    assert r.status_code == 401

def test_put_action_not_found(client):
    r = client.put(
        "/actions/nonexistent_action_xyz",
        json={"id": "nonexistent_action_xyz", "label": "X", "prompt_single": "x"},
        headers=ADMIN_HDR,
    )
    assert r.status_code == 404


# ── DELETE /actions/{action_id} ───────────────────────────────────────────────

def test_delete_action_reader_key_rejected(client):
    r = client.delete("/actions/step1_test", headers=READER_HDR)
    assert r.status_code == 401

def test_delete_action_admin_key(client):
    client.post(
        "/actions",
        json={"id": "step1_del", "label": "Del", "prompt_single": "x"},
        headers=ADMIN_HDR,
    )
    r = client.delete("/actions/step1_del", headers=ADMIN_HDR)
    assert r.status_code == 204

def test_delete_action_not_found(client):
    r = client.delete("/actions/does_not_exist_xyz", headers=ADMIN_HDR)
    assert r.status_code == 404

def test_delete_action_no_key_rejected(client):
    r = client.delete("/actions/step1_test")
    assert r.status_code == 401


# ── verifica integrità actions.yaml dopo operazioni ───────────────────────────

def test_existing_actions_still_readable(client):
    """Le azioni preesistenti (da actions.yaml) devono essere ancora visibili."""
    r = client.get("/actions", headers=READER_HDR)
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert "people_count" in ids
