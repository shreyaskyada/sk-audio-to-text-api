
import os
import httpx
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

def create_openai_client():
    """
    Create OpenAI client with explicit configuration to avoid proxy issues.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not found in environment variables")
        # Don't raise error, let it fail when used if key is missing
    
    # Check for proxy settings
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    
    if http_proxy or https_proxy:
        logger.info(f"Using proxy configuration: {http_proxy or https_proxy}")
        return OpenAI(
            api_key=api_key,
            http_client=httpx.Client(
                proxies=http_proxy or https_proxy or None,
                transport=httpx.HTTPTransport(local_address="0.0.0.0")
            )
        )
    else:
        return OpenAI(api_key=api_key)
