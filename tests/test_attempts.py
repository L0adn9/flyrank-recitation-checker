from tests.conftest import signup, silence_wav

AL_FIL = ("ألم تر كيف فعل ربك بأصحاب الفيل ألم يجعل كيدهم في تضليل وأرسل عليهم طيرا أبابيل "
          "ترميهم بحجارة من سجيل فجعلهم كعصف مأكول")


def upload(client, headers, surah=105, a=1, b=5, data=None, ctype="audio/wav"):
    return client.post("/attempts", headers=headers,
                       data={"surah": surah, "ayah_from": a, "ayah_to": b},
                       files={"audio": ("rec.wav", data if data is not None else silence_wav(), ctype)})


def test_surahs_are_public_and_cacheable(client):
    r = client.get("/surahs/105")
    assert r.status_code == 200 and len(r.json()["ayahs"]) == 5
    assert r.json()["basmala"].startswith("بِسْمِ")
    assert "max-age" in r.headers["cache-control"]
    assert client.get("/surahs/115").status_code == 400


def test_range_and_file_validation(client):
    h = signup(client)
    assert upload(client, h, surah=105, a=1, b=6).status_code == 400
    assert upload(client, h, surah=105, a=3, b=2).status_code == 400
    assert upload(client, h, surah=0).status_code == 400
    assert upload(client, h, surah=2, a=1, b=40).status_code == 400
    assert upload(client, h, data=b"").status_code == 400
    assert upload(client, h, ctype="text/plain").status_code == 415


def test_attempts_are_private_to_their_owner(client, fake):
    fake.text = AL_FIL
    sara, omar = signup(client), signup(client, email="omar@example.com", name="Omar")
    attempt_id = upload(client, sara).json()["id"]
    assert client.get(f"/attempts/{attempt_id}", headers=omar).status_code == 404
    assert client.delete(f"/attempts/{attempt_id}", headers=omar).status_code == 404
    assert client.get("/attempts", headers=omar).json() == []
    assert client.delete(f"/attempts/{attempt_id}", headers=sara).status_code == 204
    assert client.get(f"/attempts/{attempt_id}", headers=sara).status_code == 404
