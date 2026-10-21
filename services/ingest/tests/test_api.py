import json

import pytest
from ingest.vault import read_json

DAY = "2026-10-20"


def test_read_contracts_and_moment(client):
    assert client.get("/health").json()["capabilities"]["ask_mode"] == "extractive"
    assert client.get("/days").json()[0]["date"] == DAY
    day = client.get(f"/day/{DAY}").json()
    assert len(day["layers"]) == 5
    assert day["layers"][2]["floors"] == [0, 2]
    assert client.get(f"/day/{DAY}/skeleton").status_code == 200
    moment = client.get(f"/moment/mo_001?date={DAY}").json()
    assert moment["meta"]["media_available"] is False
    assert len(moment["transcript"]["segments"]) == 3


def test_ask_requires_token(client, auth):
    body = {"date": DAY, "question": "demo order"}
    assert client.post("/ask", json=body).status_code == 401
    assert client.post("/ask", json=body, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.post("/ask", json=body, headers=auth).status_code == 200


def test_ask_location_and_citations(client, auth):
    response = client.post("/ask", json={"date": DAY, "question": "Where was I when we decided the demo order?"}, headers=auth)
    answer = response.json()
    assert answer["layer_index"] == 3
    assert answer["t"] == 45605
    assert answer["moment_id"] == "mo_001"
    assert answer["offset_s"] == 5
    evidence = {record["id"]: record for record in answer["evidence"]}
    for sentence in answer["sentences"]:
        assert sentence["evidence"]
        assert sentence["text"] == evidence[sentence["evidence"][0]]["text"]


def test_unmatched_query_has_no_invented_answer(client, auth):
    answer = client.post("/ask", json={"date": DAY, "question": "unicornpancakequantum"}, headers=auth).json()
    assert answer["sentences"] == []
    assert answer["evidence"] == []
    assert answer["layer_index"] is None


def test_user_input_cannot_inject_fts_operators(client, auth):
    response = client.post("/ask", json={"date": DAY, "question": 'demo" OR * ) NEAR(order)'}, headers=auth)
    assert response.status_code == 200


def test_sse_contract(client, auth):
    response = client.post("/ask?stream=1", json={"date": DAY, "question": "demo order"}, headers=auth)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: location" in response.text
    assert "event: answer" in response.text
    assert "event: done" in response.text
    data = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]
    assert data[0]["layer_index"] == 3


def test_events_update_stats_and_search(client, auth, vault):
    event = dict(vault.events(DAY)[0], id="ev_new", label="Read the quasar notebook", text="quasar")
    response = client.post("/events", json={"date": DAY, "events": [event]}, headers=auth)
    assert response.json() == {"accepted": 1}
    assert read_json(vault.day_path(DAY) / "day.json")["stats"]["events"] == 15
    answer = client.post("/ask", json={"date": DAY, "question": "quasar"}, headers=auth).json()
    assert answer["evidence"][0]["id"] == "ev_new"
    assert client.post("/events", json={"date": DAY, "events": [event]}, headers=auth).status_code == 400


def test_invalid_event_write_is_atomic(client, auth, vault):
    good = dict(vault.events(DAY)[0], id="ev_good")
    bad = dict(good, id="ev_bad", t="2026-10-21T08:00:00-04:00")
    response = client.post("/events", json={"date": DAY, "events": [good, bad]}, headers=auth)
    assert response.status_code == 400
    assert len(vault.events(DAY)) == 14


def test_incomplete_and_extra_fields_are_rejected(client, auth, vault):
    event = next(e for e in vault.events(DAY) if e["type"] == "said.quote")
    event.pop("speaker")
    assert client.post("/events", json={"date": DAY, "events": [event]}, headers=auth).status_code == 422
    assert client.post("/ask", json={"date": DAY, "question": "demo", "api_key": "not-a-key"}, headers=auth).status_code == 422


def test_index_jobs_and_unsupported_stages(client, auth):
    response = client.post(f"/ingest/{DAY}", json={"steps": ["index"]}, headers=auth)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "complete"
    assert "event: progress" in client.get(f"/jobs/{job_id}?stream=1").text
    assert client.post(f"/ingest/{DAY}", json={"steps": ["moments"]}, headers=auth).status_code == 501
    assert client.delete(f"/day/{DAY}/hours", headers=auth).status_code == 501
    assert client.post("/pause", headers=auth).status_code == 503


@pytest.mark.parametrize("ref", [".token", "day.json", "index.sqlite", "moments/mo_001/transcript.json", "screens/../../.token"])
def test_assets_never_serve_private_files(client, ref):
    assert client.get(f"/vault/{DAY}/{ref}").status_code in {400, 404}


def test_assets_serve_only_supported_media(client):
    assert client.get(f"/vault/{DAY}/screens/1005_00.svg").status_code == 200


def test_rename_updates_viewer_and_search(client, auth):
    assert client.post("/places/rename", json={"place_id": "hall", "name": "Observatory"}, headers=auth).status_code == 200
    assert client.get(f"/day/{DAY}").json()["layers"][2]["name"] == "Observatory"
    answer = client.post("/ask", json={"date": DAY, "question": "Observatory"}, headers=auth).json()
    assert answer["evidence"][0]["place_name"] == "Observatory"


def test_speaker_confirm_is_persisted(client, auth, vault):
    response = client.post("/speakers/confirm", json={"date": DAY, "moment_id": "mo_001", "diar_label": "SPEAKER_00", "person_id": "person:sam"}, headers=auth)
    assert response.status_code == 200
    transcript = read_json(vault.day_path(DAY) / "moments/mo_001/transcript.json")
    assert transcript["speaker_map"]["SPEAKER_00"] == "person:sam"


def test_cors_does_not_allow_untrusted_pages(client):
    response = client.options("/ask", headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in response.headers
