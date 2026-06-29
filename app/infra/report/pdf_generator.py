"""
Gera PDF do relatório de investigação usando ReportLab.
"""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

from app.domain.models.compliance import ComplianceReport

# Paleta de cores
COLOR_DARK = colors.HexColor("#1a1a2e")
COLOR_ACCENT = colors.HexColor("#16213e")
COLOR_BLUE = colors.HexColor("#0f3460")
COLOR_LIGHT_BLUE = colors.HexColor("#e8f4f8")
COLOR_RED = colors.HexColor("#c0392b")
COLOR_ORANGE = colors.HexColor("#e67e22")
COLOR_YELLOW = colors.HexColor("#f39c12")
COLOR_GREEN = colors.HexColor("#27ae60")
COLOR_GRAY = colors.HexColor("#7f8c8d")
COLOR_LIGHT_GRAY = colors.HexColor("#f5f6fa")

RISK_COLORS = {
    "CRITICAL": COLOR_RED,
    "HIGH": COLOR_ORANGE,
    "MEDIUM": COLOR_YELLOW,
    "LOW": COLOR_GREEN,
    "N/A": COLOR_GRAY,
}


def _build_styles():
    base = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontSize=20,
            textColor=COLOR_DARK,
            spaceAfter=4,
            fontName="Helvetica-Bold",
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontSize=11,
            textColor=COLOR_GRAY,
            spaceAfter=2,
            fontName="Helvetica",
        ),
        "section_title": ParagraphStyle(
            "SectionTitle",
            parent=base["Heading2"],
            fontSize=13,
            textColor=colors.white,
            fontName="Helvetica-Bold",
            spaceAfter=6,
            spaceBefore=16,
            leftIndent=0,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=9,
            textColor=COLOR_DARK,
            leading=14,
            spaceAfter=4,
            fontName="Helvetica",
            alignment=TA_JUSTIFY,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["Normal"],
            fontSize=9,
            textColor=COLOR_DARK,
            leading=13,
            spaceAfter=2,
            fontName="Helvetica",
            leftIndent=14,
            bulletIndent=4,
        ),
        "field_label": ParagraphStyle(
            "FieldLabel",
            parent=base["Normal"],
            fontSize=8,
            textColor=COLOR_GRAY,
            fontName="Helvetica-Bold",
            spaceAfter=1,
        ),
        "field_value": ParagraphStyle(
            "FieldValue",
            parent=base["Normal"],
            fontSize=9,
            textColor=COLOR_DARK,
            fontName="Helvetica",
            spaceAfter=5,
        ),
        "ref": ParagraphStyle(
            "Ref",
            parent=base["Normal"],
            fontSize=7,
            textColor=COLOR_GRAY,
            fontName="Helvetica",
            leading=10,
            spaceAfter=2,
        ),
        "risk_badge": ParagraphStyle(
            "RiskBadge",
            parent=base["Normal"],
            fontSize=10,
            textColor=colors.white,
            fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["Normal"],
            fontSize=7,
            textColor=COLOR_GRAY,
            fontName="Helvetica",
            alignment=TA_CENTER,
        ),
    }
    return styles


def _risk_label(risk: str) -> str:
    labels = {
        "CRITICAL": "CRÍTICO",
        "HIGH": "ALTO",
        "MEDIUM": "MÉDIO",
        "LOW": "BAIXO",
        "N/A": "N/A",
    }
    return labels.get(risk, risk)


def _section_header(title: str, risk: str, styles: dict):
    """Retorna o cabeçalho colorido de uma seção."""
    risk_color = RISK_COLORS.get(risk, COLOR_GRAY)
    header_data = [[
        Paragraph(f"<b>{title}</b>", styles["section_title"]),
        Paragraph(f"<b>{_risk_label(risk)}</b>", styles["risk_badge"]),
    ]]
    t = Table(header_data, colWidths=[13.5 * cm, 3 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_BLUE),
        ("BACKGROUND", (1, 0), (1, 0), risk_color),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 8),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return t


def _parse_content_to_paragraphs(content: str, styles: dict) -> list:
    """Converte markdown simples em elementos ReportLab."""
    elements = []
    for line in content.split("\n"):
        line = line.rstrip()
        if not line:
            elements.append(Spacer(1, 4))
            continue

        # Detecta e converte negrito **text**
        import re
        line_rl = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)

        if line.startswith("**") and line.endswith("**"):
            # Linha inteiramente em negrito = subseção
            elements.append(Paragraph(line_rl, styles["field_label"]))
        elif line.startswith("  - ") or line.startswith("    - "):
            elements.append(Paragraph(f"• {line_rl.strip()[2:]}", styles["bullet"]))
        elif line.startswith("- ") or line.startswith("• "):
            elements.append(Paragraph(f"• {line_rl[2:]}", styles["bullet"]))
        elif re.match(r"^\d+\.\s", line):
            elements.append(Paragraph(line_rl, styles["bullet"]))
        else:
            elements.append(Paragraph(line_rl, styles["body"]))

    return elements


def generate_pdf(report: ComplianceReport) -> bytes:
    """Gera o PDF do relatório e retorna os bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2 * cm,
        title=f"Relatório de Investigação — {report.query}",
    )

    styles = _build_styles()
    story = []

    # ------------------------------------------------------------------
    # Capa / Cabeçalho
    # ------------------------------------------------------------------
    story.append(HRFlowable(width="100%", thickness=3, color=COLOR_BLUE, spaceAfter=8))

    story.append(Paragraph("RELATÓRIO DE INVESTIGAÇÃO", styles["subtitle"]))
    story.append(Paragraph(report.query, styles["title"]))

    gen_dt = report.generated_at[:10] if report.generated_at else datetime.now().strftime("%Y-%m-%d")
    story.append(Paragraph(f"Gerado em: {gen_dt}  |  Risco geral: <b>{_risk_label(report.overall_risk)}</b>", styles["subtitle"]))

    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_LIGHT_BLUE, spaceBefore=6, spaceAfter=10))

    # Cartão de risco
    risk_color = RISK_COLORS.get(report.overall_risk, COLOR_GRAY)
    risk_data = [[
        Paragraph("NÍVEL DE RISCO GERAL", styles["field_label"]),
        Paragraph(f"<b>{_risk_label(report.overall_risk)}</b>", styles["risk_badge"]),
        Paragraph(f"Processos: {report.raw_process_count}", styles["field_label"]),
        Paragraph(f"Chunks: {report.graph_stats.chunks}", styles["field_label"]),
    ]]
    risk_table = Table(risk_data, colWidths=[4.5 * cm, 3.5 * cm, 4 * cm, 4.5 * cm])
    risk_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_LIGHT_GRAY),
        ("BACKGROUND", (1, 0), (1, 0), risk_color),
        ("TEXTCOLOR", (1, 0), (1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, COLOR_GRAY),
    ]))
    story.append(risk_table)
    story.append(Spacer(1, 14))

    # ------------------------------------------------------------------
    # Seções do relatório
    # ------------------------------------------------------------------
    _REGISTROS_SECTIONS = {"participacoes_societarias", "vinculos_profissionais"}
    _registros_header_added = False

    for section_key, section in report.sections.items():
        risk = section.risk_level or "N/A"

        # Insert "Registros Relevantes" group header before first of its subsections
        if section_key in _REGISTROS_SECTIONS and not _registros_header_added:
            _registros_header_added = True
            group_style = ParagraphStyle(
                "GroupHeader",
                parent=getSampleStyleSheet()["Normal"],
                fontSize=11,
                textColor=COLOR_BLUE,
                fontName="Helvetica-Bold",
                spaceBefore=14,
                spaceAfter=4,
                leftIndent=0,
            )
            story.append(Paragraph("Registros Relevantes", group_style))
            story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_LIGHT_BLUE, spaceAfter=6))

        header = _section_header(section.title, risk, styles)
        content_els = _parse_content_to_paragraphs(section.content or "", styles)

        block = [header, Spacer(1, 4)]
        block.extend(content_els)
        block.append(Spacer(1, 8))

        story.append(KeepTogether(block[:6]))  # mantém header + primeiros parágrafos juntos
        story.extend(block[6:])

    # ------------------------------------------------------------------
    # Referências
    # ------------------------------------------------------------------
    if report.references:
        story.append(Spacer(1, 8))
        ref_header = _section_header("Referências", "N/A", styles)
        story.append(ref_header)
        story.append(Spacer(1, 4))
        for ref in report.references:
            label = ref.get("label", "")
            url = ref.get("url", "")
            num = ref.get("num", "")
            story.append(Paragraph(
                f"[{num}] <b>{label}</b><br/>"
                f"<font color='#0f3460'>{url}</font>",
                styles["ref"],
            ))
            story.append(Spacer(1, 2))

    # ------------------------------------------------------------------
    # Rodapé
    # ------------------------------------------------------------------
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_LIGHT_BLUE))
    story.append(Paragraph(
        f"Documento gerado automaticamente pelo sistema de investigação corporativa. "
        f"Gerado em {gen_dt}. As informações são baseadas em fontes públicas e podem conter homônimos.",
        styles["footer"],
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
