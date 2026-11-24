from tests.conftest import signup, silence_wav

AL_FIL = ("ألم تر كيف فعل ربك بأصحاب الفيل ألم يجعل كيدهم في تضليل وأرسل عليهم طيرا أبابيل "
          "ترميهم بحجارة من سجيل فجعلهم كعصف مأكول")

QURAYSH = "لإيلاف قريش إيلافهم رحلة الشتاء والصيف فليعبدوا رب هذا البيت الذي أطعمهم من جوع وآمنهم من خوف"


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


def test_segmentation_adapts_to_the_noise_floor():
    from app.audio import FRAME, speech_spans
    # 1 s speech, 0.6 s pause, 1 s speech, 0.2 s breath, 1 s speech, on a noisy floor (-50 dB) or a quiet one (-80 dB)
    for floor in (-50.0, -80.0):
        pattern = [-20.0] * 50 + [floor] * 30 + [-20.0] * 50 + [floor] * 10 + [-20.0] * 50
        spans = speech_spans(pattern)
        assert len(spans) == 2, floor  # split at the pause, not at the breath
        assert abs(spans[0][1] - 50 * FRAME) < 1e-6
