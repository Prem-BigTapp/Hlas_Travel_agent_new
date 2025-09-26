import logging
import json
import httpx
import os
from datetime import datetime
from app.session_manager import get_session, set_stage, get_collected_info

logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "https://api-sandbox.hlas.com.sg")
QUOTE_ENDPOINT = "/api/v2/quotation/generate"
TEST_MODE = os.getenv("TEST_MODE", "True").lower() == "false"

def _call_generate_quote_api_mock(quote_request: dict) -> dict:
    """ Mocks the API call with a SIMPLE response. """
    logger.warning("--- MOCK API CALL to /api/v2/quotation/generate ---")
    plan = quote_request.get("travel", {}).get("plan", "gold")
    mock_response = {
        "timestamp": datetime.now().isoformat(),
        "success": "true",
        "warnings": [], "errors": [],
        "data": { "premiums": { plan: {"discounted_premium": 40.5} } }
    }
    logger.info(f"Mock API Response: {json.dumps(mock_response, indent=2)}")
    return mock_response

def _call_generate_quote_api(quote_request: dict) -> dict:
    """ Calls the REAL quotation API or returns a mock if in Test Mode. """
    if TEST_MODE:
        return _call_generate_quote_api_mock(quote_request)
    
    logger.info(f"--- Calling REAL API: {API_BASE_URL}{QUOTE_ENDPOINT} ---")
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(f"{API_BASE_URL}{QUOTE_ENDPOINT}", json=quote_request)
            response.raise_for_status() 
            response_data = response.json()
            logger.info(f"Real API Response: {json.dumps(response_data, indent=2)}")
            return response_data
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error calling quote API: {e.response.status_code} - {e.response.text}")
        return {"success": "false", "errors": [f"HTTP error: {e.response.status_code}"]}
    except Exception as e:
        logger.error(f"Unknown error calling quote API: {e}")
        return {"success": "false", "errors": [f"Unknown error: {e}"]}

def run_quote_generation(session_id: str) -> dict:
    """ 
    Retrieves the final payload, sends it for a quote, and parses the response.
    """
    try:
        collected_info = get_collected_info(session_id)
        final_payload = collected_info.get("payload")

        if not final_payload:
            set_stage(session_id, "payload_collection") 
            return {"output": "I seem to have lost your details. Let's start over."}

        quote_response = _call_generate_quote_api(final_payload)
        
        if quote_response.get("success") not in ["ok", "true"]:
            errors = quote_response.get("errors", ["Unknown API error"])
            return {"output": f"Sorry, there was an error getting the quote: {errors[0]}"}
        
        # --- NEW ROBUST PARSING LOGIC ---
        plan_tier = final_payload.get("travel", {}).get("plan", "basic")
        price_str = "Price not available"
        
        # Safely navigate the nested JSON response to find the price
        data = quote_response.get("data")
        if data and isinstance(data, dict):
            premiums = data.get("premiums")
            if premiums and isinstance(premiums, dict):
                plan_info = premiums.get(plan_tier.lower())
                if plan_info and isinstance(plan_info, dict):
                    final_price = plan_info.get("discounted_premium")
                    # Safely format the price into a string
                    try:
                        price_str = f"S${float(final_price):.2f}"
                    except (ValueError, TypeError):
                        logger.warning(f"Could not format price: {final_price}")
        
        final_message = f"Your quote for the **{plan_tier.capitalize()} Plan** has been generated. The premium is **{price_str}**."
        
        set_stage(session_id, "initial") # Reset for a new conversation
        return {"output": final_message}

    except Exception as e:
        logger.error(f"Error in run_quote_generation for session {session_id}: {str(e)}")
        set_stage(session_id, "initial") 
        return {"output": "I'm sorry, I ran into an error while generating your quote."}