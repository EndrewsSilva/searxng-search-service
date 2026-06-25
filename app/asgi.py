from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException

from app.domain.models.search import SearchRequest
from app.domain.models.compliance import ComplianceRequest
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
