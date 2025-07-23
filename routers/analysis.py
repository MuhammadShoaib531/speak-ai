from fastapi import APIRouter, HTTPException, Depends
from twilio.rest import Client
from twilio.base.exceptions import TwilioException
import os
from typing import Dict, Any, Optional
from pydantic import BaseModel

from auth import get_current_active_user
from models import User

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)

class PhoneNumberRequest(BaseModel):
    phone_number: str

class MultiplePhoneNumbersRequest(BaseModel):
    phone_numbers: list[str]
    include_recent_calls: bool = True
    include_recent_messages: bool = True

class TwilioPhoneDetails(BaseModel):
    phone_number: str
    friendly_name: str
    sid: str
    account_sid: str
    status: str
    capabilities: Dict[str, Any]
    address_requirements: str
    beta: bool
    origin: str
    trunk_sid: Optional[str] = None
    emergency_status: Optional[str] = None
    emergency_address_sid: Optional[str] = None
    date_created: str
    date_updated: str
    url: str

def get_twilio_client():
    """Initialize and return Twilio client"""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    
    if not account_sid or not auth_token:
        raise HTTPException(
            status_code=500, 
            detail="Twilio credentials not configured"
        )
    
    return Client(account_sid, auth_token)



@router.post("/twilio-multiple-numbers-analytics")
async def get_multiple_numbers_analytics(
    request: MultiplePhoneNumbersRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get analytics for multiple phone numbers at once to save time.
    Returns individual analytics for each number plus combined summary.
    """
    try:
        client = get_twilio_client()
        
        results = []
        combined_stats = {
            "total_calls": 0,
            "total_messages": 0,
            "total_duration": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "all_calls": [],
            "all_messages": []
        }
        
        for phone_number in request.phone_numbers:
            try:
                # Normalize phone number format
                normalized_phone = phone_number
                if not normalized_phone.startswith('+'):
                    normalized_phone = '+' + normalized_phone
                
                # Get calls for this number
                outgoing_calls = client.calls.list(
                    from_=normalized_phone,
                    limit=100
                )
                
                incoming_calls = client.calls.list(
                    to=normalized_phone,
                    limit=100
                )
                
                all_calls = list(outgoing_calls) + list(incoming_calls)
                
                # Get messages for this number if requested
                all_messages = []
                if request.include_recent_messages:
                    outgoing_messages = client.messages.list(
                        from_=normalized_phone,
                        limit=50
                    )
                    
                    incoming_messages = client.messages.list(
                        to=normalized_phone,
                        limit=50
                    )
                    
                    all_messages = list(outgoing_messages) + list(incoming_messages)
                
                # Calculate stats for this number
                total_calls = len(all_calls)
                total_messages = len(all_messages)
                
                # Calculate call statistics
                completed_calls = [c for c in all_calls if c.status == 'completed' and c.duration]
                total_duration = sum(int(c.duration) for c in completed_calls if c.duration)
                average_duration = total_duration / len(completed_calls) if completed_calls else 0
                
                successful_calls = len([c for c in all_calls if c.status == 'completed'])
                failed_calls = len([c for c in all_calls if c.status in ['failed', 'busy', 'no-answer', 'canceled']])
                success_rate = (successful_calls / total_calls * 100) if total_calls > 0 else 0
                failure_rate = (failed_calls / total_calls * 100) if total_calls > 0 else 0
                
                # Group calls by status
                call_statuses = {}
                for call in all_calls:
                    status = call.status
                    call_statuses[status] = call_statuses.get(status, 0) + 1
                
                # Prepare individual result
                individual_result = {
                    "phone_number": phone_number,
                    "status": "success",
                    "call_statistics": {
                        "total_calls": total_calls,
                        "completed_calls": successful_calls,
                        "failed_calls": len([c for c in all_calls if c.status == 'failed']),
                        "busy_calls": len([c for c in all_calls if c.status == 'busy']),
                        "no_answer_calls": len([c for c in all_calls if c.status == 'no-answer']),
                        "canceled_calls": len([c for c in all_calls if c.status == 'canceled']),
                        "average_call_duration_seconds": round(average_duration, 2),
                        "total_call_duration_seconds": total_duration,
                        "success_rate_percentage": round(success_rate, 2),
                        "failure_rate_percentage": round(failure_rate, 2)
                    },
                    "message_statistics": {
                        "total_messages": total_messages,
                        "delivered_messages": len([m for m in all_messages if m.status == 'delivered']),
                        "failed_messages": len([m for m in all_messages if m.status == 'failed']),
                        "sent_messages": len([m for m in all_messages if m.status == 'sent'])
                    }
                }
                
                # Add recent calls and messages if requested
                if request.include_recent_calls:
                    individual_result["recent_calls"] = [
                        {
                            "to": call.to,
                            "from": call.from_formatted,
                            "status": call.status,
                            "duration_seconds": int(call.duration) if call.duration else 0,
                            "duration_formatted": f"{int(call.duration) // 60}m {int(call.duration) % 60}s" if call.duration else "0s",
                            "date_created": call.date_created.isoformat() if call.date_created else "",
                            "direction": call.direction
                        } for call in all_calls[:5]  # Show only 5 recent calls per number
                    ]
                
                if request.include_recent_messages:
                    individual_result["recent_messages"] = [
                        {
                            "to": msg.to,
                            "from": msg.from_,
                            "status": msg.status,
                            "direction": msg.direction,
                            "date_created": msg.date_created.isoformat() if msg.date_created else ""
                        } for msg in all_messages[:5]  # Show only 5 recent messages per number
                    ]
                
                results.append(individual_result)
                
                # Add to combined stats
                combined_stats["total_calls"] += total_calls
                combined_stats["total_messages"] += total_messages
                combined_stats["total_duration"] += total_duration
                combined_stats["successful_calls"] += successful_calls
                combined_stats["failed_calls"] += failed_calls
                combined_stats["all_calls"].extend(all_calls)
                combined_stats["all_messages"].extend(all_messages)
                
            except Exception as e:
                # If one number fails, include error but continue with others
                results.append({
                    "phone_number": phone_number,
                    "status": "error",
                    "error": str(e),
                    "call_statistics": None,
                    "message_statistics": None
                })
        
        # Calculate combined summary
        combined_success_rate = (combined_stats["successful_calls"] / combined_stats["total_calls"] * 100) if combined_stats["total_calls"] > 0 else 0
        combined_failure_rate = (combined_stats["failed_calls"] / combined_stats["total_calls"] * 100) if combined_stats["total_calls"] > 0 else 0
        combined_avg_duration = combined_stats["total_duration"] / combined_stats["successful_calls"] if combined_stats["successful_calls"] > 0 else 0
        
        return {
            "request_summary": {
                "total_numbers_requested": len(request.phone_numbers),
                "successful_numbers": len([r for r in results if r["status"] == "success"]),
                "failed_numbers": len([r for r in results if r["status"] == "error"]),
                "include_recent_calls": request.include_recent_calls,
                "include_recent_messages": request.include_recent_messages
            },
            "combined_summary": {
                "total_calls_across_all_numbers": combined_stats["total_calls"],
                "total_messages_across_all_numbers": combined_stats["total_messages"],
                "total_duration_seconds": combined_stats["total_duration"],
                "total_duration_formatted": f"{combined_stats['total_duration'] // 3600}h {(combined_stats['total_duration'] % 3600) // 60}m {combined_stats['total_duration'] % 60}s",
                "successful_calls_across_all_numbers": combined_stats["successful_calls"],
                "failed_calls_across_all_numbers": combined_stats["failed_calls"],
                "combined_success_rate_percentage": round(combined_success_rate, 2),
                "combined_failure_rate_percentage": round(combined_failure_rate, 2),
                "combined_average_duration_seconds": round(combined_avg_duration, 2),
                "combined_average_duration_formatted": f"{int(combined_avg_duration) // 60}m {int(combined_avg_duration) % 60}s" if combined_avg_duration else "0s"
            },
            "individual_results": results
        }
        
    except TwilioException as e:
        raise HTTPException(
            status_code=400,
            detail=f"Twilio API error: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
