from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import cockpit, companies, console, console_feishu_cli_routes, feishu, health, mail, operations, portal, user_identity_oauth_routes, v5, work_events

app = FastAPI(
    title="Digital Advisor OS",
    description="Enterprise Digital Chief of Staff platform for Feishu-centered operations, memory, reasoning, and reporting.",
    version="0.5.0",
)

app.include_router(health.router)
app.include_router(companies.router)
app.include_router(cockpit.router)
app.include_router(feishu.router)
app.include_router(mail.router)
app.include_router(user_identity_oauth_routes.router)
app.include_router(work_events.router)
app.include_router(operations.router)
app.include_router(v5.router)
app.include_router(console.router)
app.include_router(console_feishu_cli_routes.router)
app.include_router(portal.router)
app.mount("/console/assets", StaticFiles(directory="app/static/console"), name="console-assets")
app.mount("/portal/assets", StaticFiles(directory="app/static/portal"), name="portal-assets")
