from app.core.config import settings
from app.services.pipeline import PipelineService

pipeline_service = PipelineService(cameras_config_path=settings.cameras_config_path)
