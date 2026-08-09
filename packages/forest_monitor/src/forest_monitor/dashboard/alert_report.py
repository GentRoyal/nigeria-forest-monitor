"""Generate concise PDF alert reports from scored grid cells."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from loguru import logger
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from forest_monitor.config import resolve_path
from forest_monitor.detection.risk_scorer import top_alerts


def generate_alert_report(
    scored_grid: gpd.GeoDataFrame,
    config: dict,
    output_path: str | Path | None = None,
    *,
    limit: int = 20,
) -> Path:
    """Write a PDF containing the highest-risk cells."""
    alerts = top_alerts(scored_grid, limit=limit)
    if output_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output = resolve_path(config, "reports", create=True) / f"forest_alert_{stamp}.pdf"
    else:
        output = Path(output_path)
        if not output.is_absolute():
            output = resolve_path(config, "reports", create=True) / output
        output.parent.mkdir(parents=True, exist_ok=True)

    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Nigeria Forest Monitor Alert Report",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Nigeria Forest Monitor — Alert Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
            f"{int(scored_grid.get('alert', False).sum())} cells at or above the alert threshold.",
            styles["BodyText"],
        ),
        Spacer(1, 8 * mm),
    ]

    columns = ["Rank", "Cell", "Zone", "Risk", "Level", "Change", "Classifier", "ACLED"]
    rows = [columns]
    for row in alerts.itertuples():
        rows.append([
            int(getattr(row, "risk_rank", len(rows))),
            str(row.cell_id),
            str(getattr(row, "zone", "")),
            f"{float(row.risk_score):.3f}",
            str(row.risk_level),
            f"{float(getattr(row, 'change_score', 0)):.3f}",
            f"{float(getattr(row, 'classifier_score', 0)):.3f}",
            f"{float(getattr(row, 'acled_score', 0)):.3f}",
        ])

    table = Table(rows, repeatRows=1, colWidths=[13*mm, 18*mm, 31*mm, 18*mm, 20*mm, 21*mm, 23*mm, 18*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b5e20")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    story.extend([
        Spacer(1, 7 * mm),
        Paragraph(
            "Risk scores are decision-support indicators, not proof of hostile activity. "
            "High-risk cells require analyst review and corroboration with current intelligence.",
            styles["Italic"],
        ),
    ])
    document.build(story)
    logger.success(f"Alert report saved -> {output}")
    return output


__all__ = ["generate_alert_report"]