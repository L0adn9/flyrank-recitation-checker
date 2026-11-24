from app.db import connection
from tests.conftest import run_jobs, signup, silence_wav
from tests.test_attempts import AL_FIL, upload


def test_report_pdf_is_built_in_the_background(client, fake):
    h = signup(client)
    fake.text = AL_FIL.replace("طيرا", "طيور")
    upload(client, h)
    run_jobs()
    fake.text = AL_FIL
    upload(client, h, data=silence_wav(1.5))
    run_jobs()

    r = client.post("/reports", headers=h, json={"days": 30})
    assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["download_url"] is None
    rid = r.json()["id"]
    assert client.get(f"/reports/{rid}/file", headers=h).status_code == 409
    run_jobs()
    report = client.get(f"/reports/{rid}", headers=h).json()
    assert report["status"] == "done" and report["pages"] >= 1
    pdf = client.get(report["download_url"], headers=h)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and pdf.content.rstrip().endswith(b"%%EOF")


def test_report_html_names_the_most_missed_word(client, fake):
    from app.report import gather, render_html
    h = signup(client)
    fake.text = AL_FIL.replace("طيرا", "طيور")
    upload(client, h)
    run_jobs()
    with connection() as conn:
        user = conn.execute("SELECT id, email, name FROM users").fetchone()
        data = gather(conn, user, 30)
    assert data["checks"] == 1 and data["top_missed"][0]["text"] == "طَيْرًا"
    assert data["top_missed"][0]["how"] == "wrong"
    html = render_html(data)
    assert "Where the mistakes are" in html and "طَيْرًا" in html


def test_double_click_does_not_queue_two_reports(client):
    h = signup(client)
    first = client.post("/reports", headers=h)
    second = client.post("/reports", headers=h)
    assert first.status_code == 202 and second.status_code == 200 and first.json()["id"] == second.json()["id"]


def test_reports_are_private_and_validated(client):
    h = signup(client)
    other = signup(client, email="omar@example.com", name="Omar")
    rid = client.post("/reports", headers=h).json()["id"]
    assert client.get(f"/reports/{rid}", headers=other).status_code == 404
    assert client.get(f"/reports/{rid}/file", headers=other).status_code == 404
    assert client.post("/reports", headers=other, json={"days": 3}).status_code == 400
