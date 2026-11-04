"""YAI Guard Web/API control plane.

The app object is assembled here only: settings are read by settings.py,
routes live in routes.py, and startup maintenance is wired from maintenance.py.

Run:
    MOCK_MODEL=1 SANDBOX_DRIVER=fake uvicorn app.main:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from app import maintenance, routes, settings

app = FastAPI(title="YAI Guard", version=settings.APP_VERSION)
app.include_router(routes.router)
# This FastAPI version stores an included router as a wrapper in app.routes.
# Flatten it so the externally inspected route table stays path-for-path
# identical to the pre-refactor app.
_included = app.router.routes.pop()
app.router.routes.extend(_included.original_router.routes)
app.on_event("startup")(maintenance.start_background_tasks)
