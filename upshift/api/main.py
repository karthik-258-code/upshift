"""FastAPI application factory. Run with: uvicorn upshift.api.main:create_app --factory"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from upshift import __version__
from upshift.config import ROOT, Settings, get_settings
from upshift.core.bus import EventBus, usage_publisher
from upshift.core.events import Event, EventType, Run, RunStatus
from upshift.core.store import EventStore
from upshift.llm.client import TokenFactoryClient
from upshift.llm.models import ModelCatalog
from upshift.llm.router import ModelRouter

log = logging.getLogger(__name__)


class AppContext:
    def __init__(
        self,
        settings: Settings,
        store: EventStore,
        bus: EventBus,
        catalog: ModelCatalog,
        router: ModelRouter,
        llm: TokenFactoryClient | None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bus = bus
        self.catalog = catalog
        self.router = router
        self.llm = llm


Launcher = Callable[[Run, AppContext], Awaitable[None]]


class CreateRunRequest(BaseModel):
    repo_url: str = Field(..., min_length=1)
    policy: Literal["conservative", "balanced", "aggressive"] = "balanced"
    budget_cap_usd: float | None = Field(default=None, gt=0, le=50)
    packages: list[str] = Field(default_factory=list)
    target_project: str | None = None


async def pipeline_launcher(run: Run, ctx: AppContext) -> None:
    """Run the real agent chain. Requires Token Factory and Tavily credentials."""
    from upshift.agents.supervisor import build_supervisor
    from upshift.sandbox.factory import build_executor

    assert ctx.llm is not None
    supervisor = build_supervisor(
        ctx.settings, ctx.store, ctx.bus, ctx.llm, ctx.router, build_executor(ctx.settings)
    )
    await supervisor.run(run)


async def noop_launcher(run: Run, ctx: AppContext) -> None:
    """Day-0 placeholder: records lifecycle events so the UI and SSE path can be exercised."""
    ctx.store.set_status(run.id, RunStatus.RUNNING)
    await ctx.bus.publish(
        Event(
            run_id=run.id,
            type=EventType.RUN_STARTED,
            payload={
                "repo_url": run.repo_url,
                "policy": run.policy,
                "budget_cap_usd": run.budget_cap_usd,
            },
        )
    )
    await ctx.bus.publish(
        Event(
            run_id=run.id,
            type=EventType.AGENT_MESSAGE,
            agent="supervisor",
            payload={"message": "Agent pipeline not wired yet; recording lifecycle events only."},
        )
    )
    ctx.store.set_status(run.id, RunStatus.SUCCEEDED, mode="noop")
    await ctx.bus.publish(
        Event(run_id=run.id, type=EventType.RUN_FINISHED, payload={"status": "noop"})
    )


def create_app(
    settings: Settings | None = None,
    *,
    store: EventStore | None = None,
    launcher: Launcher | None = None,
    background: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    store = store or EventStore(settings.runs_db)
    bus = EventBus(store)
    catalog = ModelCatalog.load(settings.models_file, settings.capabilities_file)
    router = ModelRouter(catalog)

    on_usage = usage_publisher(bus)

    llm: TokenFactoryClient | None = None
    if settings.nebius_api_key:
        llm = TokenFactoryClient(
            settings.nebius_api_key,
            settings.nebius_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            usage_hook=on_usage,
        )

    ctx = AppContext(settings, store, bus, catalog, router, llm)
    launch: Launcher
    if launcher is not None:
        launch = launcher
    elif llm is not None and settings.tavily_api_key:
        launch = pipeline_launcher
    else:
        launch = noop_launcher
    tasks: set[asyncio.Task[None]] = set()

    app = FastAPI(title="Upshift", version=__version__)
    app.state.ctx = ctx

    async def guarded(run: Run, coro: Awaitable[None]) -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s crashed", run.id)
            store.set_status(run.id, RunStatus.FAILED, error=str(exc))
            await bus.publish(
                Event(
                    run_id=run.id,
                    type=EventType.RUN_FAILED,
                    payload={"error": f"{type(exc).__name__}: {exc}"},
                )
            )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "nebius_configured": bool(settings.nebius_api_key),
            "tavily_configured": bool(settings.tavily_api_key),
            "capabilities_probed": catalog.probed,
            "executor": settings.executor,
            "gitlab_configured": bool(settings.gitlab_token),
            "github_configured": bool(settings.github_token),
            "pipeline": "agents" if launch is pipeline_launcher else "noop",
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {
            "probed": catalog.probed,
            "models": [
                {
                    "key": spec.key,
                    "id": spec.id,
                    "tier": spec.tier,
                    "context_tokens": spec.context_tokens,
                    "input_price_per_m": spec.input_price_per_m,
                    "output_price_per_m": spec.output_price_per_m,
                    "capabilities": sorted(catalog.capabilities(spec.key)),
                    "available": catalog.is_available(spec.key),
                    "notes": spec.notes,
                }
                for spec in catalog.models.values()
            ],
        }

    @app.get("/api/runs")
    async def list_runs(limit: int = 50) -> dict[str, Any]:
        return {"runs": store.list_runs(limit=min(max(limit, 1), 200))}

    @app.post("/api/runs", status_code=201)
    async def create_run(req: CreateRunRequest) -> Run:
        run = Run(
            repo_url=req.repo_url,
            policy=req.policy,
            budget_cap_usd=req.budget_cap_usd or settings.default_budget_usd,
            packages=req.packages,
            target_project=req.target_project,
        )
        store.create_run(run)
        coro = guarded(run, launch(run, ctx))
        if background:
            task = asyncio.create_task(coro)
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        else:
            await coro
        return store.get_run(run.id) or run

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> Run:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @app.get("/api/runs/{run_id}/events")
    async def get_events(run_id: str, after: int = 0) -> dict[str, Any]:
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"events": store.events(run_id, after_seq=after)}

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        if store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        last_event_id = request.headers.get("last-event-id")
        if last_event_id and last_event_id.isdigit():
            after = int(last_event_id)

        async def generate() -> Any:
            async for event in bus.stream(run_id, after_seq=after):
                if await request.is_disconnected():
                    break
                yield event.to_sse() if event is not None else ": keepalive\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    web_dist = ROOT / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        async def index() -> dict[str, str]:
            return {
                "name": "Upshift",
                "version": __version__,
                "docs": "/docs",
                "health": "/api/health",
                "ui": "frontend not built yet (web/dist missing)",
            }

    return app
