"""Step 3 — cameras.yaml senza credenziali + CRUD cameras."""
import pytest
from tests.conftest import ADMIN_HDR, READER_HDR


# ── verifica assenza credenziali ──────────────────────────────────────────────

def test_cameras_response_no_credentials(client):
    """La risposta GET /cameras non deve mai esporre username o password."""
    r = client.get("/cameras", headers=READER_HDR)
    assert r.status_code == 200
    for cam in r.json():
        assert "username" not in cam
        assert "password" not in cam


def test_cameras_list_not_empty(client):
    r = client.get("/cameras", headers=READER_HDR)
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_no_duplicate_tc_lobby(client):
    """tc_lobby deve apparire una sola volta."""
    r = client.get("/cameras", headers=READER_HDR)
    ids = [c["id"] for c in r.json()]
    assert ids.count("tc_lobby") == 1


def test_tc_kitchen_cabinet_has_recording_diskid(client):
    r = client.get("/cameras", headers=READER_HDR)
    cams = {c["id"]: c for c in r.json()}
    assert "tc_kitchen_cabinet" in cams
    assert cams["tc_kitchen_cabinet"]["recording_diskid"] == "SD_DISK"


# ── auth CRUD ─────────────────────────────────────────────────────────────────

def test_post_camera_reader_rejected(client):
    payload = {"id": "test_cam", "label": "Test", "base_url": "http://1.2.3.4"}
    r = client.post("/cameras", json=payload, headers=READER_HDR)
    assert r.status_code == 401


def test_post_camera_admin_accepted(client):
    payload = {
        "id": "step3_test_cam",
        "label": "Step3 Camera",
        "base_url": "http://10.0.0.99",
        "channel": 1,
        "resolution": "640x480",
        "compression": 50,
        "rotation": 0,
    }
    r = client.post("/cameras", json=payload, headers=ADMIN_HDR)
    assert r.status_code in (201, 409)


def test_post_camera_duplicate_returns_409(client):
    payload = {"id": "step3_dup_cam", "label": "Dup", "base_url": "http://10.0.0.1"}
    client.post("/cameras", json=payload, headers=ADMIN_HDR)
    r = client.post("/cameras", json=payload, headers=ADMIN_HDR)
    assert r.status_code == 409


def test_post_camera_no_credentials_in_body(client):
    """CameraIn non deve accettare username/password — Pydantic deve ignorarli."""
    payload = {
        "id": "step3_nocreds",
        "label": "NoCreds",
        "base_url": "http://10.0.0.2",
        "username": "shouldbeignored",
        "password": "shouldbeignored",
    }
    r = client.post("/cameras", json=payload, headers=ADMIN_HDR)
    assert r.status_code in (201, 409)


def test_put_camera_admin(client):
    client.post(
        "/cameras",
        json={"id": "step3_put_cam", "label": "Old", "base_url": "http://10.0.0.3"},
        headers=ADMIN_HDR,
    )
    r = client.put(
        "/cameras/step3_put_cam",
        json={"id": "step3_put_cam", "label": "Updated", "base_url": "http://10.0.0.3"},
        headers=ADMIN_HDR,
    )
    assert r.status_code == 200
    assert r.json()["label"] == "Updated"


def test_put_camera_not_found(client):
    r = client.put(
        "/cameras/nonexistent_camera_xyz",
        json={"id": "nonexistent_camera_xyz", "label": "X", "base_url": "http://10.0.0.99"},
        headers=ADMIN_HDR,
    )
    assert r.status_code == 404


def test_delete_camera_admin(client):
    client.post(
        "/cameras",
        json={"id": "step3_del_cam", "label": "Del", "base_url": "http://10.0.0.4"},
        headers=ADMIN_HDR,
    )
    r = client.delete("/cameras/step3_del_cam", headers=ADMIN_HDR)
    assert r.status_code == 204
    cams = client.get("/cameras", headers=READER_HDR).json()
    assert not any(c["id"] == "step3_del_cam" for c in cams)


def test_delete_camera_reader_rejected(client):
    r = client.delete("/cameras/tc_kitchen", headers=READER_HDR)
    assert r.status_code == 401


def test_delete_camera_not_found(client):
    r = client.delete("/cameras/nonexistent_xyz", headers=ADMIN_HDR)
    assert r.status_code == 404
