import logging
import json
from dpath import set as dpath_set, get as dpath_get
from typing import Optional
from datetime import datetime

from app.config import llm
from langchain_core.messages import SystemMessage, HumanMessage
from app.session_manager import get_session, set_collected_info, set_stage, update_conversation_context

logger = logging.getLogger(__name__)

def get_payload_template() -> dict:
    """Returns the master JSON payload structure as a Python dictionary."""
    return {
        "_internal": { "start_date": None, "end_date": None },
        "ProductCode": "TVP",
        "media": {"wcc": "HLS"},
        "travel": {
            "policy_type": None, "country_code": [], "number_of_days": None,
            "zone": None, "with_children": None, "with_spouse": "no",
            "with_group_of_adults": None, "with_group_of_households": "no",
            "plan": "basic",
            "selectedAddOns": {
                "preExAddOn": {"selected": None, "preselected": False},
                "lossFFMAddOn": {"selected": None, "preselected": False},
                "flightDelayAddOn": {"selected": None, "preselected": False}
            },
            "number_of_travellers": { "total": None, "child": [], "adult": [], "group": 1 }
        },
        "promotion": {"coupon_code": None},
        "leads": { "email": None, "contact_mobile": None },
        "CEPParams": {}
    }

def get_question_map() -> dict:
    """Maps payload keys to user-facing questions."""
    return {
        'travel/policy_type': "To start, what is the policy type? (Enter 'S' for Single Trip or 'A' for Annual)",
        '_internal/start_date': "What is your travel start date (YYYY-MM-DD)?",
        '_internal/end_date': "And what is your travel end date (YYYY-MM-DD)?",
        'travel/country_code': "What is the 3-letter country code for your destination (e.g., 'MAL')?",
        'travel/number_of_travellers/adult': "How many adults are traveling?",
        'travel/number_of_travellers/child': "How many children are traveling?",
        'travel/selectedAddOns/preExAddOn/selected': "Do you require coverage for pre-existing conditions? (true/false)",
        'travel/selectedAddOns/lossFFMAddOn/selected': "Add coverage for Loss of Frequent Flyer Miles? (true/false)",
        'travel/selectedAddOns/flightDelayAddOn/selected': "Add the Flight Delay benefit? (true/false)",
        'leads/email': "What is your email address?",
        'leads/contact_mobile': "What is your 8-digit contact mobile number?",
        'promotion/coupon_code': "Finally, do you have a coupon code? (If not, just say 'no')",
    }

def find_next_question_key(payload: dict) -> Optional[str]:
    """Finds the first key in the payload that needs to be filled."""
    for key in get_question_map().keys():
        try:
            value = dpath_get(payload, key)
            if value is None or value == []: return key
        except KeyError: return key
    return None

def run_travel_payload_agent(user_message: str, chat_history: list, session_id: str) -> dict:
    session = get_session(session_id)
    collected_info = session.get("collected_info", {})
    payload = collected_info.get("payload")

    if payload is None:
        payload = get_payload_template()
    
    context = session.get("conversation_context", {})
    last_question_key = context.get("last_question_key")

    if last_question_key and user_message.strip().lower() not in ["hi", "hello"]:
        answer = user_message.strip()

        # --- Step 1: Validate input before processing ---
        if last_question_key in ['_internal/start_date', '_internal/end_date']:
            try:
                date_str = answer.replace('/', '-')
                datetime.strptime(date_str, "%Y-%m-%d")
                answer = date_str
            except ValueError:
                logger.warning(f"Invalid date format received: {user_message}")
                question_map = get_question_map()
                re_ask_question = question_map[last_question_key]
                return {"output": f"That doesn't look like a valid date format. Please use YYYY-MM-DD.\n\n{re_ask_question}"}

        # --- Step 2: Process and save the validated answer ---
        try:
            if last_question_key == 'travel/policy_type':
                if answer.lower() == 's': answer = 'single'
                elif answer.lower() == 'a': answer = 'annual'
            elif answer.lower() in ['true', 'false']:
                answer = answer.lower() == 'true'
            elif answer.lower() == 'no' and last_question_key == 'promotion/coupon_code':
                answer = ""
            elif answer.isdigit():
                answer = int(answer)
            
            if last_question_key in ['travel/number_of_travellers/adult', 'travel/number_of_travellers/child']:
                if last_question_key == 'travel/number_of_travellers/adult': payload['travel']['number_of_travellers']['adult'] = [answer]
                else: payload['travel']['number_of_travellers']['child'] = [answer]
                
                adults = payload['travel']['number_of_travellers']['adult'][0] if payload['travel']['number_of_travellers']['adult'] else 0
                children = payload['travel']['number_of_travellers']['child'][0] if payload['travel']['number_of_travellers']['child'] else 0
                payload['travel']['number_of_travellers']['total'] = adults + children
                payload['travel']['with_children'] = "yes" if children > 0 else "no"
                payload['travel']['with_group_of_adults'] = "yes" if adults > 1 else "no"
            elif last_question_key == 'travel/country_code':
                dpath_set(payload, last_question_key, [answer.upper()])
            else:
                dpath_set(payload, last_question_key, answer)
            
            logger.info(f"Payload updated for key '{last_question_key}'")
        except Exception as e:
            logger.error(f"Failed to set key {last_question_key} with value {user_message}: {e}")

    # --- Step 3: Find the next question ---
    next_key = find_next_question_key(payload)
    set_collected_info(session_id, "payload", payload)

    if next_key:
        question_map = get_question_map()
        next_question = question_map[next_key]
        update_conversation_context(session_id, last_question_key=next_key)
        return {"output": next_question}
    else:
        # --- Step 4: Finalize payload and finish ---
        start_str = payload.get("_internal", {}).get("start_date")
        end_str = payload.get("_internal", {}).get("end_date")
        if start_str and end_str:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
            end = datetime.strptime(end_str, "%Y-%m-%d").date()
            num_days = max((end - start).days + 1, 1)
            payload['travel']['number_of_days'] = num_days
            logger.info(f"Final calculation: number_of_days set to {num_days}")

        if '_internal' in payload: del payload['_internal']
        
        set_collected_info(session_id, "payload", payload)
        
        logger.info("--- FINAL POPULATED PAYLOAD ---")
        logger.info(json.dumps(payload, indent=4))
        logger.info("--- END OF PAYLOAD ---")
        
        set_stage(session_id, "quote_generation")
        update_conversation_context(session_id, last_question_key=None)
        return {"output": "Thank you, I have all the information. Generating your quote now..."}