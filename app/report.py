"""The "My progress" PDF: gather a user's numbers, render an HTML page, print it with headless Chromium."""
import base64
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from psycopg import Connection

from app.config import settings
from app.revision import today

TEMPLATES = Path(__file__).parent / "templates"
FONTS = Path(__file__).parent / "static" / "fonts"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def _local_date(ts: datetime) -> date:
    return ts.astimezone(ZoneInfo(settings.timezone)).date()


def gather(conn: Connection, user: dict, days: int) -> dict:
    end = today()
    start = end - timedelta(days=days - 1)
    attempts = conn.execute(
        """SELECT a.id, a.surah, s.name_en AS surah_name, a.ayah_from, a.ayah_to, a.accuracy,
                  a.mistake_count, a.word_count, a.result, a.created_at
           FROM attempts a JOIN surahs s ON s.number = a.surah
           WHERE a.user_id = %s AND a.status = 'done' AND a.created_at >= %s::date - 1
           ORDER BY a.created_at""",
        (user["id"], start),
    ).fetchall()
    tz = ZoneInfo(settings.timezone)
    attempts = [{**a, "created_at": a["created_at"].astimezone(tz)} for a in attempts
                if start <= _local_date(a["created_at"]) <= end]

    per_day: dict[date, list[float]] = defaultdict(list)
    for a in attempts:
        per_day[_local_date(a["created_at"])].append(a["accuracy"])
    daily = [{"day": start + timedelta(days=i),
              "checks": len(per_day.get(start + timedelta(days=i), [])),
              "accuracy": (sum(v) / len(v)) if (v := per_day.get(start + timedelta(days=i))) else None}
             for i in range(days)]

    missed: Counter = Counter()
    kinds: dict[tuple, Counter] = defaultdict(Counter)
    for a in attempts:
        for w in a["result"]["words"]:
            if w["status"] != "correct":
                key = (a["surah"], a["surah_name"], w["ayah"], w["text"])
                missed[key] += 1
                kinds[key][w["status"]] += 1
    top_missed = [{"surah": k[0], "surah_name": k[1], "ayah": k[2], "text": k[3], "count": n,
                   "how": ", ".join(f"{s} ×{c}" if c > 1 else s for s, c in kinds[k].most_common())}
                  for k, n in missed.most_common(10)]

    surahs = conn.execute(
        """SELECT p.surah, s.name_en, s.name_ar, s.ayah_count, count(*) AS checked,
                  count(*) FILTER (WHERE p.last_solid) AS solid,
                  count(*) FILTER (WHERE p.due_on <= %s) AS due,
                  min(p.due_on) FILTER (WHERE p.due_on > %s) AS next_due
           FROM ayah_progress p JOIN surahs s ON s.number = p.surah
           WHERE p.user_id = %s GROUP BY p.surah, s.name_en, s.name_ar, s.ayah_count ORDER BY p.surah""",
        (end, end, user["id"]),
    ).fetchall()

    accs = [a["accuracy"] for a in attempts]
    first_half = [a["accuracy"] for a in attempts if _local_date(a["created_at"]) < start + timedelta(days=days // 2)]
    second_half = [a["accuracy"] for a in attempts if _local_date(a["created_at"]) >= start + timedelta(days=days // 2)]
    return {
        "user": user,
        "start": start,
        "end": end,
        "days": days,
        "generated_at": datetime.now(ZoneInfo(settings.timezone)),
        "checks": len(attempts),
        "clean_checks": sum(1 for a in attempts if a["mistake_count"] == 0),
        "avg_accuracy": sum(accs) / len(accs) if accs else None,
        "trend": (sum(second_half) / len(second_half) - sum(first_half) / len(first_half))
        if first_half and second_half else None,
        "active_days": sum(1 for d in daily if d["checks"]),
        "ayahs_checked": sum(s["checked"] for s in surahs),
        "ayahs_solid": sum(s["solid"] for s in surahs),
        "ayahs_due": sum(s["due"] for s in surahs),
        "daily": daily,
        "chart": chart(daily),
        "surahs": surahs,
        "top_missed": top_missed,
        "recent": list(reversed(attempts))[:8],
    }


def chart(daily: list[dict], width: int = 640, height: int = 150) -> dict:
    """Geometry for a bar chart of daily average accuracy (0-100 %), one thin bar per day with checks."""
    left, right, top, bottom = 34, 6, 8, 22
    plot_w, plot_h = width - left - right, height - top - bottom
    step = plot_w / len(daily)
    bar_w = max(3.0, min(12.0, step * 0.55))
    bars = []
    for i, d in enumerate(daily):
        if d["accuracy"] is None:
            continue
        h = max(2.0, plot_h * d["accuracy"])
        x = left + i * step + (step - bar_w) / 2
        y = top + plot_h - h
        r = min(4.0, bar_w / 2, h)
        # Rounded top, square bottom on the baseline.
        path = (f"M{x:.1f},{top + plot_h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
                f"H{x + bar_w - r:.1f} Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + r:.1f} V{top + plot_h:.1f} Z")
        bars.append({"path": path, "title": f"{d['day']:%d %b}: {d['accuracy']:.0%} over {d['checks']} check(s)"})
    grid = [{"y": top + plot_h * (1 - v), "label": f"{int(v * 100)}%"} for v in (0, 0.5, 1)]
    ticks = []
    for i, d in enumerate(daily):
        if i == 0 or i == len(daily) - 1 or (len(daily) > 10 and d["day"].weekday() == 0 and 2 < i < len(daily) - 3):
            ticks.append({"x": left + i * step + step / 2, "label": f"{d['day']:%d %b}"})
    return {"width": width, "height": height, "left": left, "right": width - right,
            "bars": bars, "grid": grid, "ticks": ticks, "baseline": top + plot_h}


@cache
def _font_data(name: str) -> str:
    mime = "font/woff2" if name.endswith(".woff2") else "font/ttf"
    return f"data:{mime};base64," + base64.b64encode((FONTS / name).read_bytes()).decode()


def render_html(data: dict) -> str:
    return _env.get_template("report.html").render(**data, font=_font_data)


def to_pdf(html: str, out: Path) -> None:
    """Print with headless Chromium. Fonts are embedded as data URIs, so the page needs no file access."""
    from playwright.sync_api import sync_playwright

    footer = ("<div style='width:100%;font:8px sans-serif;color:#77706a;padding:0 14mm;display:flex;"
              "justify-content:space-between'><span>Recite · progress report</span>"
              "<span>Page <span class='pageNumber'></span> of <span class='totalPages'></span></span></div>")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.evaluate("document.fonts.ready")
            page.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
                     header_template="<span></span>", footer_template=footer,
                     margin={"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
        finally:
            browser.close()


def build_report(report_id: int) -> Path:
    """Worker job: build one report's PDF and mark it done."""
    from app.db import connection

    with connection() as conn:
        r = conn.execute("UPDATE reports SET status = 'processing' WHERE id = %s RETURNING *", (report_id,)).fetchone()
        conn.commit()
        if r is None:
            return Path()
        user = conn.execute("SELECT id, email, name FROM users WHERE id = %s", (r["user_id"],)).fetchone()
        data = gather(conn, user, r["days"])
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out = settings.reports_dir / f"report-{report_id}.pdf"
    to_pdf(render_html(data), out)
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", out.read_bytes()))
    with connection() as conn:
        conn.execute("UPDATE reports SET status = 'done', file_path = %s, pages = %s, error = NULL, finished_at = now()"
                     " WHERE id = %s", (str(out), pages, report_id))
        conn.commit()
    return out
