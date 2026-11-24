from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import jobs
from app.auth import current_user
from app.db import connection

router = APIRouter(prefix="/reports", tags=["reports"])
COLUMNS = "id, days, status, pages, error, created_at, finished_at"


class NewReport(BaseModel):
    days: int = Field(30, ge=7, le=365, description="How many days back the report covers")


def _owned(conn, report_id: int, user_id: int) -> dict:
    row = conn.execute(f"SELECT {COLUMNS}, file_path FROM reports WHERE id = %s AND user_id = %s",
                       (report_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, f"Report {report_id} not found")
    return row


def _public(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "file_path"}
    out["download_url"] = f"/reports/{row['id']}/file" if row["status"] == "done" else None
    return out


@router.post("", status_code=202)
def request_report(response: Response, body: NewReport | None = None, user: dict = Depends(current_user)):
    """Queue a "My progress" PDF. Building it takes a few seconds; poll GET /reports/{id}."""
    days = (body or NewReport()).days
    with connection() as conn:
        busy = conn.execute("SELECT id FROM reports WHERE user_id = %s AND status IN ('queued', 'processing')",
                            (user["id"],)).fetchone()
        if busy:  # one at a time per user: a double click should not build two PDFs
            response.status_code = 200
            return _public(_owned(conn, busy["id"], user["id"]))
        row = conn.execute(f"INSERT INTO reports (user_id, days) VALUES (%s, %s) RETURNING {COLUMNS}, file_path",
                           (user["id"], days)).fetchone()
        jobs.enqueue(conn, "build_report", {"report_id": row["id"]}, max_attempts=2)
        conn.commit()
    response.headers["Location"] = f"/reports/{row['id']}"
    return _public(row)


@router.get("")
def list_reports(user: dict = Depends(current_user)):
    with connection() as conn:
        rows = conn.execute(f"SELECT {COLUMNS}, file_path FROM reports WHERE user_id = %s ORDER BY id DESC LIMIT 20",
                            (user["id"],)).fetchall()
    return [_public(r) for r in rows]


@router.get("/{report_id}")
def get_report(report_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        return _public(_owned(conn, report_id, user["id"]))


@router.get("/{report_id}/file")
def download_report(report_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        row = _owned(conn, report_id, user["id"])
    if row["status"] != "done":
        raise HTTPException(409, f"Report {report_id} is not ready yet (status: {row['status']})")
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(410, "This report file is gone. Please create a new report")
    name = f"recite-progress-{row['created_at']:%Y-%m-%d}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=name)
