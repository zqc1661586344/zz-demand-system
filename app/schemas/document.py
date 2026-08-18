"""Document Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    file_size: int
    mime_type: str
    status: str  # pending, processing, indexed, failed
    chunk_count: int | None = None
    error_message: str | None = None
    uploaded_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    status: str = "pending"
    message: str = "File uploaded, processing started"


class ReprocessResponse(BaseModel):
    id: str
    status: str = "pending"
    message: str = "Reprocessing started"
