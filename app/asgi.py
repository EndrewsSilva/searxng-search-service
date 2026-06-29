import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import Response

from pydantic import BaseModel
from app.domain.models.search import SearchRequest
from app.domain.models.compliance import ComplianceRequest


class ComplianceReportPdfRequest(BaseModel):
    """Recebe o JSON completo retornado por POST /compliance-report."""
    report: dict
from app.infra.containers.services_container import (
    get_flaresolverr_client,
    get_jusbrasil_session,
    get_run_search_use_case,
    get_compliance_use_case,
)
from app.interface.config.settings import AppSettings

APP_SETTINGS = AppSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.infra.graph import embeddings as emb
    from app.infra.graph.schema import setup_schema
    from app.infra.containers.services_container import get_neo4j_client
    asyncio.create_task(emb.prewarm_async())
    asyncio.create_task(setup_schema(get_neo4j_client()))
    yield
    session = get_jusbrasil_session()
    if session:
        await session.close()


app = FastAPI(
    title=APP_SETTINGS.NAME,
    description=APP_SETTINGS.DESCRIPTION,
    version=APP_SETTINGS.VERSION,
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/search")
async def search(
    payload: SearchRequest,
    use_case=Depends(get_run_search_use_case)  # O FastAPI resolve e injeta os dois clientes automaticamente
):
    try:
        # Executa a busca e raspagem com a proteção do FlareSolverr ativa
        return await use_case.execute(payload.query)

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={
                "success": False,
                "error": str(e)
            }
        )


@app.post("/compliance-report")
async def compliance_report(
    payload: ComplianceRequest,
    use_case=Depends(get_compliance_use_case),
):
    try:
        report = await use_case.execute(payload.query)
        return report
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={"success": False, "error": str(e)},
        )


@app.post("/compliance-report/pdf")
async def compliance_report_pdf(
    payload: ComplianceReportPdfRequest,
):
    """
    Converte um relatório de compliance (JSON) em PDF.
    Recebe o JSON retornado por POST /compliance-report e gera o PDF.
    """
    try:
        from app.infra.report.pdf_generator import generate_pdf
        from app.domain.models.compliance import ComplianceReport as CR
        report = CR(**payload.report)
        pdf_bytes = generate_pdf(report)
        safe_name = report.query.replace(" ", "_")[:60]
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="relatorio_{safe_name}.pdf"'},
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail={"success": False, "error": str(e)})


@app.post("/resolve")
async def resolve(
    payload: dict,
    client=Depends(get_flaresolverr_client)
):
    try:
        url = payload["url"]

        html = await client.get_page(url)

        with open("/tmp/resolved_page.html", "w", encoding="utf-8") as f:
            f.write(html)

        return {
            "url": url,
            "success": True,
            "html_size": len(html),
            "html_preview": html[:2000],
            "saved_file": "/tmp/resolved_page.html"
        }

    except KeyError:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": "Campo obrigatório ausente: url"
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={
                "success": False,
                "url": payload.get("url"),
                "error": str(e)
            }
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.asgi:app",
        host=APP_SETTINGS.HOST,
        port=APP_SETTINGS.PORT
    )
