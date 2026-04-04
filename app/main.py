from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import setup_logging
from app.services.runtime import pipeline_service

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    pipeline_service.start()
    yield
    pipeline_service.stop()


app = FastAPI(title='gasvision-nn-service', lifespan=lifespan)
app.include_router(router)
