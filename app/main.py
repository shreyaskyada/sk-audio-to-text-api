import logging
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import connect_to_mongo, close_mongo_connection
from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up API...")
    await connect_to_mongo()
    yield
    # Shutdown
    logger.info("Shutting down API...")
    await close_mongo_connection()

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    formatted_process_time = f"{process_time:.2f}"
    logger.info(
        f"Method: {request.method} Path: {request.url.path} "
        f"Status: {response.status_code} Time: {formatted_process_time}ms"
    )
    return response

@app.get("/")
async def root():
    return {"message": "Welcome to Audio to Text API", "status": "running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "audio-to-text-api"}

# Register routers
from app.routes import (
    soap_routes, appointment_routes, feedback_routes, form_routes, 
    pr1_routes, work_status_routes, signature_routes, log_routes, 
    pr2_routes, transcription_routes, soap_generation_routes
)
app.include_router(soap_routes.router, prefix="/api/v1/soap-notes", tags=["SOAP Notes"])
app.include_router(appointment_routes.router, prefix="/api/v1/appointments", tags=["Appointments"])
app.include_router(feedback_routes.router, prefix="/api/v1/feedback", tags=["Feedback"])
app.include_router(form_routes.router, prefix="/api/v1", tags=["Forms"])
app.include_router(pr1_routes.router, prefix="/api/v1/pr1", tags=["PR1"])
app.include_router(work_status_routes.router, prefix="/api/v1/work-status-form", tags=["Work Status"])
app.include_router(signature_routes.router, prefix="/api/v1/patient-signatures", tags=["Patient Signatures"])
app.include_router(log_routes.router, prefix="/api/v1/logs", tags=["Logs"])
app.include_router(pr2_routes.router, prefix="/api/v1/pr2", tags=["PR2"])

# Backward compatibility - Include old API routes temporarily until frontend is updated
logger.info("Loading legacy API routes for backward compatibility...")
# Note: Legacy routes for SOAP, Appointments, Intake, and Followup are removed to avoid routing conflicts (405 Method Not Allowed).
# The new refactored routes should be used instead.

# Include Transcription and SOAP Generation routes (No prefix, as they have absolute paths defined)
app.include_router(transcription_routes.router, tags=["Transcription"])
app.include_router(soap_generation_routes.router, tags=["SOAP Generation"])
