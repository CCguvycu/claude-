"""FastAPI application: JSON API + SSE activity stream + static frontend.

Binds to 127.0.0.1 by default. The Claude credential lives only in this process
and is never included in any response.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agents.manager import TaskManager
from ..agents.roles import CATALOG
from ..config import SETTINGS, Settings
from ..db import Database
from ..events import EventBus
from ..tools import git_tools

STATIC = Path(__file__).resolve().parent.parent / "static"


class ProjectIn(BaseModel):
    path: str = Field(min_length=1)
    name: str | None = None


class TaskIn(BaseModel):
    project_id: int
    request: str = Field(min_length=3, max_length=20000)


class CommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    paths: list[str] | None = None


class ConfirmIn(BaseModel):
    approve: bool


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or SETTINGS
    database = db or Database(settings.db_path)
    bus = EventBus()
    manager = TaskManager(database, settings, bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A task cannot survive a server restart; mark orphans so the UI is honest.
        for row in database.running_tasks():
            database.update_task(row["id"], status="failed",
                                 error="server restarted while the task was running")
        yield
        await manager.shutdown()
        database.close()

    app = FastAPI(title="ARKX Forge", version="1.0.0", lifespan=lifespan)
    app.state.db = database
    app.state.manager = manager
    app.state.settings = settings
    app.state.bus = bus

    # ---------------------------------------------------------------- meta
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": app.version}

    @app.get("/api/config")
    async def config() -> dict:
        cfg = settings.public_dict()
        cfg["agents"] = CATALOG
        return cfg

    @app.get("/api/agents")
    async def agents() -> list[dict]:
        return CATALOG

    # ------------------------------------------------------------ projects
    def _project_payload(row: dict) -> dict:
        root = Path(row["path"])
        payload = dict(row)
        payload["exists"] = root.is_dir()
        payload["git"] = git_tools.snapshot(root) if payload["exists"] else {"is_repo": False}
        payload["running_task"] = manager.is_busy(row["id"])
        return payload

    @app.get("/api/projects")
    async def list_projects() -> list[dict]:
        return [_project_payload(p) for p in database.list_projects()]

    @app.post("/api/projects")
    async def add_project(body: ProjectIn) -> dict:
        root = Path(body.path).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        if not root.is_dir():
            raise HTTPException(400, f"not a directory: {root}")
        name = body.name or root.name or str(root)
        return _project_payload(database.upsert_project(name, str(root.resolve())))

    @app.get("/api/projects/{pid}")
    async def get_project(pid: int) -> dict:
        row = database.get_project(pid)
        if not row:
            raise HTTPException(404, "no such project")
        return _project_payload(row)

    @app.delete("/api/projects/{pid}")
    async def remove_project(pid: int) -> dict:
        if manager.is_busy(pid):
            raise HTTPException(409, "a task is running for this project")
        database.delete_project(pid)
        return {"ok": True}

    @app.get("/api/projects/{pid}/git")
    async def project_git(pid: int) -> dict:
        row = database.get_project(pid)
        if not row:
            raise HTTPException(404, "no such project")
        return git_tools.snapshot(Path(row["path"]))

    @app.get("/api/projects/{pid}/diff")
    async def project_diff(pid: int, path: str | None = None, staged: bool = False) -> dict:
        row = database.get_project(pid)
        if not row:
            raise HTTPException(404, "no such project")
        return {"diff": git_tools.diff(Path(row["path"]), path, staged)}

    @app.post("/api/projects/{pid}/commit")
    async def project_commit(pid: int, body: CommitIn) -> dict:
        row = database.get_project(pid)
        if not row:
            raise HTTPException(404, "no such project")
        result = git_tools.stage_and_commit(Path(row["path"]), body.message, body.paths)
        if not result["ok"]:
            raise HTTPException(400, result["error"])
        return result

    # --------------------------------------------------------------- tasks
    @app.get("/api/tasks")
    async def list_tasks(project_id: int | None = None, limit: int = 50) -> list[dict]:
        return database.list_tasks(project_id, min(limit, 500))

    @app.post("/api/tasks")
    async def create_task(body: TaskIn) -> dict:
        project = database.get_project(body.project_id)
        if not project:
            raise HTTPException(404, "no such project")
        if settings.provider == "anthropic" and not settings.api_key:
            raise HTTPException(
                400, "ANTHROPIC_API_KEY is not set in the server environment. "
                     "Export it and restart Forge, or run with FORGE_PROVIDER=mock.")
        try:
            task_id = await manager.start(project, body.request)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"task_id": task_id}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: int) -> dict:
        detail = database.task_detail(task_id)
        if not detail:
            raise HTTPException(404, "no such task")
        detail["pending_confirm"] = manager.pending_for(task_id)
        detail["live"] = task_id in manager.runs
        return detail

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: int) -> dict:
        if not manager.cancel(task_id):
            raise HTTPException(409, "task is not running")
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/confirm")
    async def confirm_task(task_id: int, body: ConfirmIn) -> dict:
        if not manager.resolve_confirm(task_id, body.approve):
            raise HTTPException(409, "nothing is awaiting confirmation for this task")
        return {"ok": True, "approved": body.approve}

    @app.get("/api/pending")
    async def pending() -> list[dict]:
        return manager.all_pending()

    # -------------------------------------------------------------- stream
    @app.get("/api/stream")
    async def stream(request: Request, task_id: int | None = None) -> StreamingResponse:
        queue = bus.subscribe()

        async def gen():
            try:
                for ev in bus.backlog(task_id):
                    yield f"data: {json.dumps(ev.to_dict())}\n\n"
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"   # keeps proxies from closing the stream
                        continue
                    if task_id is not None and ev.task_id != task_id:
                        continue
                    yield f"data: {json.dumps(ev.to_dict())}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    # -------------------------------------------------------------- static
    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

        @app.get("/")
        async def index() -> Any:
            return FileResponse(str(STATIC / "index.html"))

    return app


def __getattr__(name: str):
    """Lazy `forge.server.app:app` for `uvicorn forge.server.app:app`.

    Built on first access rather than at import, so importing this module never
    opens a database or registers a second event sink.
    """
    if name == "app":
        global _APP
        try:
            return _APP
        except NameError:
            _APP = create_app()
            return _APP
    raise AttributeError(name)
