
import time
import traceback
import logging
from datetime import datetime
from fastapi import Request
from fastapi.responses import JSONResponse
from app.mongodb import get_database

logger = logging.getLogger(__name__)

async def log_requests_and_exceptions(request: Request, call_next):
    """
    Global middleware to log requests and catch exceptions.
    Logs to MongoDB collection 'api_logs' if available.
    """
    start_time = time.time()
    
    # Prepare base log data
    log_entry = {
        "timestamp": datetime.utcnow(),
        "method": request.method,
        "url": str(request.url),
        "client_host": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }
    
    try:
        response = await call_next(request)
        
        # Add response details
        process_time = (time.time() - start_time) * 1000
        log_entry["status_code"] = response.status_code
        log_entry["process_time_ms"] = round(process_time, 2)
        
        # Log to MongoDB if connected
        db = get_database()
        if db is not None:
             try:
                await db.api_logs.insert_one(log_entry)
             except Exception as log_error:
                # console log fallback
                pass
            
        return response
        
    except Exception as e:
        # Calculate time
        process_time = (time.time() - start_time) * 1000
        
        # Capture error details
        error_msg = str(e)
        stack_trace = traceback.format_exc()
        
        # Update log entry
        log_entry["status_code"] = 500
        log_entry["process_time_ms"] = round(process_time, 2)
        log_entry["error_message"] = error_msg
        log_entry["stack_trace"] = stack_trace
        
        # Log to console
        logger.error(f"🔥 Global Exception: {error_msg}")
        logger.error(stack_trace)
        
        # Log to MongoDB
        db = get_database()
        if db is not None:
            try:
                await db.api_logs.insert_one(log_entry)
            except:
                pass
            
        # Return sanitized error response
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "message": "An unexpected error occurred. This event has been logged.",
                # In dev mode we might want to show more, but for safety hide trace
                "path": str(request.url.path)
            }
        )
