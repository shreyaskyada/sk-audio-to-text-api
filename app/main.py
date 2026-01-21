import logging
import time
import traceback
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import connect_to_mongo, close_mongo_connection
from app.routes import (
    auth_routes, transcription_routes, soap_routes,
    feedback_routes, pr1_routes, intake_routes,
    followup_routes, log_routes, common_routes,
    appointment_routes, patient_routes,
    work_status_routes, pr2_routes
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.TITLE,
    version=settings.VERSION,
    description="Refactored Medical Transcription & SOAP Note API"
)

# CORS Metadata
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware for logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(f"Request: {request.method} {request.url} - Status: {response.status_code} - Time: {process_time:.4f}s")
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"Request: {request.method} {request.url} - Error: {e} - Time: {process_time:.4f}s")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise

# Database events
@app.on_event("startup")
async def startup_db_client():
    await connect_to_mongo()

@app.on_event("shutdown")
async def shutdown_db_client():
    await close_mongo_connection()

# Include Routers
app.include_router(common_routes.router)
app.include_router(auth_routes.router)
app.include_router(transcription_routes.router)
app.include_router(soap_routes.router)
app.include_router(feedback_routes.router)
app.include_router(pr1_routes.router)
app.include_router(intake_routes.router)
app.include_router(followup_routes.router)
app.include_router(log_routes.router)
app.include_router(appointment_routes.router)
app.include_router(patient_routes.router)
app.include_router(work_status_routes.router)
app.include_router(pr2_routes.router)
