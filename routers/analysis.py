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

@router.post("/twilio-phone-details", response_model=TwilioPhoneDetails)
async def get_twilio_phone_details(
    request: PhoneNumberRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all details for a Twilio phone number
    """
    try:
        client = get_twilio_client()
        
        # Search for the phone number in incoming phone numbers
        incoming_numbers = client.incoming_phone_numbers.list(
            phone_number=request.phone_number
        )
        
        if not incoming_numbers:
            raise HTTPException(
                status_code=404,
                detail=f"Phone number {request.phone_number} not found in Twilio account"
            )
        
        # Get the first matching number (should be unique)
        phone_number = incoming_numbers[0]
        
        # Extract all available details
        phone_details = TwilioPhoneDetails(
            phone_number=phone_number.phone_number,
            friendly_name=phone_number.friendly_name or "",
            sid=phone_number.sid,
            account_sid=phone_number.account_sid,
            status=phone_number.status,
            capabilities={
                "voice": phone_number.capabilities.get('voice', False) if phone_number.capabilities else False,
                "sms": phone_number.capabilities.get('sms', False) if phone_number.capabilities else False,
                "mms": phone_number.capabilities.get('mms', False) if phone_number.capabilities else False
            },
            address_requirements=phone_number.address_requirements or "",
            beta=phone_number.beta or False,
            origin=phone_number.origin or "",
            trunk_sid=phone_number.trunk_sid,
            emergency_status=phone_number.emergency_status,
            emergency_address_sid=phone_number.emergency_address_sid,
            date_created=phone_number.date_created.isoformat() if phone_number.date_created else "",
            date_updated=phone_number.date_updated.isoformat() if phone_number.date_updated else "",
            url=phone_number.uri or ""
        )
        
        return phone_details
        
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

@router.get("/twilio-all-numbers")
async def get_all_twilio_numbers(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get all phone numbers in the Twilio account
    """
    try:
        client = get_twilio_client()
        
        # Get all incoming phone numbers
        incoming_numbers = client.incoming_phone_numbers.list()
        
        numbers_list = []
        for number in incoming_numbers:
            number_info = {
                "phone_number": number.phone_number,
                "friendly_name": number.friendly_name or "",
                "sid": number.sid,
                "status": number.status,
                "capabilities": {
                    "voice": number.capabilities.get('voice', False) if number.capabilities else False,
                    "sms": number.capabilities.get('sms', False) if number.capabilities else False,
                    "mms": number.capabilities.get('mms', False) if number.capabilities else False
                },
                "date_created": number.date_created.isoformat() if number.date_created else "",
                "date_updated": number.date_updated.isoformat() if number.date_updated else ""
            }
            numbers_list.append(number_info)
        
        return {
            "total_numbers": len(numbers_list),
            "numbers": numbers_list
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

@router.post("/twilio-number-usage")
async def get_phone_number_usage(
    request: PhoneNumberRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get usage statistics for a specific phone number
    """
    try:
        client = get_twilio_client()
        
        # Normalize phone number format (ensure it starts with +)
        phone_number = request.phone_number
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number
        
        # Get call records for this number (both incoming and outgoing)
        outgoing_calls = client.calls.list(
            from_=phone_number,
            limit=50  # Limit to recent 50 outgoing calls
        )
        
        incoming_calls = client.calls.list(
            to=phone_number,
            limit=50  # Limit to recent 50 incoming calls
        )
        
        # Combine all calls
        all_calls = list(outgoing_calls) + list(incoming_calls)
        
        # Get message records for this number (both incoming and outgoing)
        outgoing_messages = client.messages.list(
            from_=phone_number,
            limit=50  # Limit to recent 50 outgoing messages
        )
        
        incoming_messages = client.messages.list(
            to=phone_number,
            limit=50  # Limit to recent 50 incoming messages
        )
        
        # Combine all messages
        all_messages = list(outgoing_messages) + list(incoming_messages)
        
        # Calculate usage statistics
        total_calls = len(all_calls)
        total_messages = len(all_messages)
        
        # Calculate call duration statistics
        completed_calls = [c for c in all_calls if c.status == 'completed' and c.duration]
        total_duration = sum(int(c.duration) for c in completed_calls if c.duration)
        average_duration = total_duration / len(completed_calls) if completed_calls else 0
        
        # Calculate success and failure rates
        successful_calls = len([c for c in all_calls if c.status == 'completed'])
        failed_calls = len([c for c in all_calls if c.status in ['failed', 'busy', 'no-answer', 'canceled']])
        success_rate = (successful_calls / total_calls * 100) if total_calls > 0 else 0
        failure_rate = (failed_calls / total_calls * 100) if total_calls > 0 else 0
        
        call_stats = {
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
        }
        
        message_stats = {
            "total_messages": total_messages,
            "delivered_messages": len([m for m in all_messages if m.status == 'delivered']),
            "failed_messages": len([m for m in all_messages if m.status == 'failed']),
            "sent_messages": len([m for m in all_messages if m.status == 'sent'])
        }
        
        return {
            "phone_number": request.phone_number,
            "call_statistics": call_stats,
            "message_statistics": message_stats,
            "recent_calls": [
                {
                    "to": call.to,
                    "from": call.from_formatted,
                    "status": call.status,
                    "direction": call.direction,
                    "duration_seconds": int(call.duration) if call.duration else 0,
                    "duration_formatted": f"{int(call.duration) // 60}m {int(call.duration) % 60}s" if call.duration else "0s",
                    "date_created": call.date_created.isoformat() if call.date_created else ""
                } for call in all_calls[:10]  # Show only recent 10 calls
            ],
            "recent_messages": [
                {
                    "to": msg.to,
                    "from": msg.from_,
                    "status": msg.status,
                    "direction": msg.direction,
                    "date_created": msg.date_created.isoformat() if msg.date_created else ""
                } for msg in all_messages[:10]  # Show only recent 10 messages
            ]
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

@router.post("/twilio-call-analytics")
async def get_call_analytics(
    request: PhoneNumberRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get detailed call analytics for a specific phone number including:
    - Total calls
    - Average call duration  
    - Success rate
    - Failure rate
    """
    try:
        client = get_twilio_client()
        
        # Normalize phone number format (ensure it starts with +)
        phone_number = request.phone_number
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number
        
        # Get call records for this number (both incoming and outgoing, increased limit for better analytics)
        outgoing_calls = client.calls.list(
            from_=phone_number,
            limit=250  # Get more outgoing calls for better analytics
        )
        
        incoming_calls = client.calls.list(
            to=phone_number,
            limit=250  # Get more incoming calls for better analytics
        )
        
        # Combine all calls
        calls = list(outgoing_calls) + list(incoming_calls)
        
        total_calls = len(calls)
        
        if total_calls == 0:
            return {
                "phone_number": request.phone_number,
                "total_calls": 0,
                "average_call_duration_seconds": 0,
                "success_rate_percentage": 0,
                "failure_rate_percentage": 0,
                "analytics": {
                    "completed": 0,
                    "failed": 0,
                    "busy": 0,
                    "no_answer": 0,
                    "canceled": 0,
                    "in_progress": 0
                },
                "duration_analytics": {
                    "total_duration_seconds": 0,
                    "average_duration_seconds": 0,
                    "shortest_call_seconds": 0,
                    "longest_call_seconds": 0
                }
            }
        
        # Group calls by status
        call_statuses = {}
        durations = []
        
        for call in calls:
            status = call.status
            call_statuses[status] = call_statuses.get(status, 0) + 1
            
            if call.duration and call.status == 'completed':
                durations.append(int(call.duration))
        
        # Calculate success and failure rates
        successful_calls = call_statuses.get('completed', 0)
        failed_calls = sum(call_statuses.get(status, 0) for status in ['failed', 'busy', 'no-answer', 'canceled'])
        
        success_rate = (successful_calls / total_calls * 100) if total_calls > 0 else 0
        failure_rate = (failed_calls / total_calls * 100) if total_calls > 0 else 0
        
        # Calculate duration statistics
        total_duration = sum(durations)
        average_duration = total_duration / len(durations) if durations else 0
        shortest_call = min(durations) if durations else 0
        longest_call = max(durations) if durations else 0
        
        return {
            "phone_number": request.phone_number,
            "total_calls": total_calls,
            "average_call_duration_seconds": round(average_duration, 2),
            "success_rate_percentage": round(success_rate, 2),
            "failure_rate_percentage": round(failure_rate, 2),
            "analytics": {
                "completed": call_statuses.get('completed', 0),
                "failed": call_statuses.get('failed', 0),
                "busy": call_statuses.get('busy', 0),
                "no_answer": call_statuses.get('no-answer', 0),
                "canceled": call_statuses.get('canceled', 0),
                "in_progress": call_statuses.get('in-progress', 0),
                "other_statuses": {k: v for k, v in call_statuses.items() 
                                if k not in ['completed', 'failed', 'busy', 'no-answer', 'canceled', 'in-progress']}
            },
            "duration_analytics": {
                "total_duration_seconds": total_duration,
                "total_duration_formatted": f"{total_duration // 3600}h {(total_duration % 3600) // 60}m {total_duration % 60}s",
                "average_duration_seconds": round(average_duration, 2),
                "average_duration_formatted": f"{int(average_duration) // 60}m {int(average_duration) % 60}s" if average_duration else "0s",
                "shortest_call_seconds": shortest_call,
                "longest_call_seconds": longest_call,
                "completed_calls_with_duration": len(durations)
            },
            "recent_calls": [
                {
                    "to": call.to,
                    "from": call.from_formatted,
                    "status": call.status,
                    "duration_seconds": int(call.duration) if call.duration else 0,
                    "duration_formatted": f"{int(call.duration) // 60}m {int(call.duration) % 60}s" if call.duration else "0s",
                    "date_created": call.date_created.isoformat() if call.date_created else "",
                    "direction": call.direction
                } for call in calls[:20]  # Show recent 20 calls
            ]
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
