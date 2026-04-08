from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.bootstrap import seed_default_monitor_sources
from app.config import get_settings
from app.database import AsyncSessionLocal, init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.utils.logger import setup_logger

settings = get_settings()
setup_logger()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_default_monitor_sources(session)
    await start_scheduler()
    try:
        yield
    finally:
        await stop_scheduler()


app = FastAPI(
    title=settings.app_name,
    version='0.2.0',
    debug=settings.debug,
    lifespan=lifespan,
)
app.include_router(router)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / 'index.html')


@app.get('/manage', include_in_schema=False)
async def manage_page() -> FileResponse:
    return FileResponse(STATIC_DIR / 'manage.html')
