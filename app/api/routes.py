from fastapi import APIRouter

from app.services.runtime import pipeline_service

router = APIRouter()


@router.get('/health')
def health() -> dict:
    return {'status': 'ok'}


@router.get('/status')
def status() -> dict:
    return pipeline_service.status()
