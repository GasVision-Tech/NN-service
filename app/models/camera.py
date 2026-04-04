from pydantic import BaseModel, Field


class CameraConfig(BaseModel):
    station_code: str = Field(..., min_length=1)
    camera_code: str = Field(..., min_length=1)
    rtsp_url: str = Field(..., min_length=1)
    enabled: bool = True
