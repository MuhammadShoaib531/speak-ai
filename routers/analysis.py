from fastapi import APIRouter, HTTPException, Depends
from twilio.rest import Client
from twilio.base.exceptions import TwilioException
import os
from typing import Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
from collections import defaultdict

from auth import get_current_active_user
from models import User
from database import get_agents_by_user_id

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)

class PhoneNumberRequest(BaseModel):
    phone_number: str

class MultiplePhoneNumbersRequest(BaseModel):
    phone_numbers: Optional[list[str]] = None
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


@router.post("/dashboard-analytics")
async def get_dashboard_analytics(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get analytics data formatted for dashboard display.
    Returns total calls, success rate, average duration, active agents count,
    call patterns, weekly performance, and agent performance data.
    """
    try:
        client = get_twilio_client()
        
        # Get user's agents from database
        user_agents = get_agents_by_user_id(current_user.id)
        if not user_agents:
            raise HTTPException(
                status_code=404,
                detail="No agents found for current user"
            )
        
        # Extract phone numbers and create agent mapping
        phone_numbers = []
        agent_phone_mapping = {}
        
        for agent in user_agents:
            if agent.twilio_number:
                phone_numbers.append(agent.twilio_number)
                agent_phone_mapping[agent.twilio_number] = {
                    "agent_type": agent.agent_type or "Unknown",
                    "agent_name": agent.agent_name,
                    "agent_id": agent.agent_id
                }
        
        if not phone_numbers:
            raise HTTPException(
                status_code=404,
                detail="No phone numbers found to analyze"
            )
        
        # Initialize data structures
        all_calls = []
        agent_stats = {}
        weekly_calls = {"Mon": 0, "Tue": 0, "Wed": 0, "Thu": 0, "Fri": 0, "Sat": 0, "Sun": 0}
        hourly_calls = defaultdict(int)
        hourly_successful_calls = defaultdict(int)
        
        # Get current date for weekly analysis (last 7 days)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        # Process each phone number
        for phone_number in phone_numbers:
            try:
                # Normalize phone number format
                normalized_phone = phone_number
                if not normalized_phone.startswith('+'):
                    normalized_phone = '+' + normalized_phone
                
                # Get calls for this number (last 7 days)
                outgoing_calls = client.calls.list(
                    from_=normalized_phone,
                    start_time_after=start_date,
                    limit=1000
                )
                
                incoming_calls = client.calls.list(
                    to=normalized_phone,
                    start_time_after=start_date,
                    limit=1000
                )
                
                phone_calls = list(outgoing_calls) + list(incoming_calls)
                all_calls.extend(phone_calls)
                
                # Initialize agent stats
                agent_info = agent_phone_mapping.get(phone_number, {})
                agent_name = agent_info.get("agent_name", f"Agent {phone_number[-4:]}")
                agent_type = agent_info.get("agent_type", "Unknown")
                
                agent_stats[phone_number] = {
                    "agent_name": agent_name,
                    "agent_type": agent_type,
                    "total_calls": len(phone_calls),
                    "successful_calls": 0,
                    "total_duration": 0,
                    "average_duration": 0,
                    "success_rate": 0
                }
                
                # Process calls for this agent
                successful_calls = 0
                total_duration = 0
                
                for call in phone_calls:
                    # Weekly performance data
                    if call.date_created:
                        day_name = call.date_created.strftime("%a")
                        weekly_calls[day_name] += 1
                        
                        # Hourly pattern data
                        hour = call.date_created.hour
                        hourly_calls[hour] += 1
                        
                        if call.status == 'completed':
                            hourly_successful_calls[hour] += 1
                    
                    # Agent performance data
                    if call.status == 'completed':
                        successful_calls += 1
                        if call.duration:
                            total_duration += int(call.duration)
                
                # Update agent stats
                agent_stats[phone_number]["successful_calls"] = successful_calls
                agent_stats[phone_number]["total_duration"] = total_duration
                agent_stats[phone_number]["success_rate"] = round(
                    (successful_calls / len(phone_calls) * 100) if phone_calls else 0, 1
                )
                agent_stats[phone_number]["average_duration"] = round(
                    (total_duration / successful_calls) if successful_calls > 0 else 0, 0
                )
                
            except Exception as e:
                print(f"Error processing phone number {phone_number}: {str(e)}")
                continue
        
        # Calculate overall statistics
        total_calls = len(all_calls)
        successful_calls = len([c for c in all_calls if c.status == 'completed'])
        total_duration = sum(int(c.duration) for c in all_calls if c.duration and c.status == 'completed')
        
        overall_success_rate = round((successful_calls / total_calls * 100) if total_calls > 0 else 0, 1)
        average_call_duration = round((total_duration / successful_calls) if successful_calls > 0 else 0, 0)
        active_agent_count = len([stats for stats in agent_stats.values() if stats["total_calls"] > 0])
        
        # Format call patterns data (hourly from 9 AM to 5 PM)
        call_patterns = []
        for hour in range(9, 18):  # 9 AM to 5 PM
            hour_12 = hour if hour <= 12 else hour - 12
            period = "AM" if hour < 12 else "PM"
            if hour == 12:
                period = "PM"
            
            time_label = f"{hour_12} {period}"
            total_hour_calls = hourly_calls.get(hour, 0)
            successful_hour_calls = hourly_successful_calls.get(hour, 0)
            
            call_patterns.append({
                "time": time_label,
                "total_calls": total_hour_calls,
                "successful_calls": successful_hour_calls
            })
        
        # Format weekly performance data
        weekly_performance = [
            {"day": "Mon", "calls": weekly_calls["Mon"]},
            {"day": "Tue", "calls": weekly_calls["Tue"]},
            {"day": "Wed", "calls": weekly_calls["Wed"]},
            {"day": "Thu", "calls": weekly_calls["Thu"]},
            {"day": "Fri", "calls": weekly_calls["Fri"]},
            {"day": "Sat", "calls": weekly_calls["Sat"]},
            {"day": "Sun", "calls": weekly_calls["Sun"]}
        ]
        
        # Format agent performance data
        agent_performance = []
        for phone_number, stats in agent_stats.items():
            if stats["total_calls"] > 0:  # Only include agents with calls
                avg_minutes = int(stats["average_duration"]) // 60
                avg_seconds = int(stats["average_duration"]) % 60
                
                agent_performance.append({
                    "agent_name": stats["agent_name"],
                    "agent_type": stats["agent_type"],
                    "phone_number": phone_number,
                    "total_calls": stats["total_calls"],
                    "success_rate": f"{stats['success_rate']}%",
                    "success_rate_value": stats["success_rate"],
                    "average_duration": f"{avg_minutes}:{avg_seconds:02d} avg",
                    "average_duration_seconds": stats["average_duration"]
                })
        
        # Sort agents by total calls (descending)
        agent_performance.sort(key=lambda x: x["total_calls"], reverse=True)
        
        # Get agent types summary
        agent_types = {}
        for agent in user_agents:
            agent_type = agent.agent_type or "Unknown"
            agent_types[agent_type] = agent_types.get(agent_type, 0) + 1
        
        return {
            "overview": {
                "total_calls": total_calls,
                "success_rate": f"{overall_success_rate}%",
                "success_rate_value": overall_success_rate,
                "average_call_duration": f"{int(average_call_duration) // 60}:{int(average_call_duration) % 60:02d}",
                "average_call_duration_seconds": average_call_duration,
                "active_agent_count": active_agent_count
            },
            "call_patterns": call_patterns,
            "weekly_performance": weekly_performance,
            "agent_performance": agent_performance,
            "agent_types": agent_types,
            "data_period": {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "days": 7
            }
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


@router.post("/training/agent-individual-analytics")
async def get_agent_individual_analytics(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get individual analytics for each agent with specific details:
    agent_name, agent_type, total_calls, success_rate, average_call_duration, 
    created_at, and last_call_time (relative time like '4 hours ago').
    """
    try:
        client = get_twilio_client()
        
        # Get user's agents from database
        user_agents = get_agents_by_user_id(current_user.id)
        if not user_agents:
            raise HTTPException(
                status_code=404,
                detail="No agents found for current user"
            )
        
        # Extract phone numbers and create agent mapping
        phone_numbers = []
        agent_phone_mapping = {}
        
        for agent in user_agents:
            if agent.twilio_number:
                phone_numbers.append(agent.twilio_number)
                agent_phone_mapping[agent.twilio_number] = {
                    "agent_type": agent.agent_type or "Unknown",
                    "agent_name": agent.agent_name,
                    "agent_id": agent.agent_id,
                    "created_at": agent.created_at
                }
        
        if not phone_numbers:
            raise HTTPException(
                status_code=404,
                detail="No phone numbers found to analyze"
            )
        
        # Helper function to calculate relative time
        def get_relative_time(timestamp):
            if not timestamp:
                return "Never"
            
            now = datetime.now()
            # Handle timezone-aware timestamps
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is not None:
                # Convert to UTC then to local time
                timestamp = timestamp.replace(tzinfo=None)
            
            diff = now - timestamp
            
            if diff.days > 0:
                if diff.days == 1:
                    return "1 day ago"
                return f"{diff.days} days ago"
            elif diff.seconds >= 3600:
                hours = diff.seconds // 3600
                if hours == 1:
                    return "1 hour ago"
                return f"{hours} hours ago"
            elif diff.seconds >= 60:
                minutes = diff.seconds // 60
                if minutes == 1:
                    return "1 minute ago"
                return f"{minutes} minutes ago"
            else:
                return "Just now"
        
        # Process each phone number
        individual_results = []
        
        for phone_number in phone_numbers:
            try:
                # Normalize phone number format
                normalized_phone = phone_number
                if not normalized_phone.startswith('+'):
                    normalized_phone = '+' + normalized_phone
                
                # Get calls for this number (last 30 days for better analysis)
                end_date = datetime.now()
                start_date = end_date - timedelta(days=30)
                
                outgoing_calls = client.calls.list(
                    from_=normalized_phone,
                    start_time_after=start_date,
                    limit=1000
                )
                
                incoming_calls = client.calls.list(
                    to=normalized_phone,
                    start_time_after=start_date,
                    limit=1000
                )
                
                all_calls = list(outgoing_calls) + list(incoming_calls)
                
                # Get agent information
                agent_info = agent_phone_mapping.get(phone_number, {})
                agent_name = agent_info.get("agent_name", f"Agent {phone_number[-4:]}")
                agent_type = agent_info.get("agent_type", "Unknown")
                created_at = agent_info.get("created_at")
                
                # Calculate statistics
                total_calls = len(all_calls)
                successful_calls = len([c for c in all_calls if c.status == 'completed'])
                success_rate = round((successful_calls / total_calls * 100) if total_calls > 0 else 0, 1)
                
                # Calculate average call duration
                completed_calls_with_duration = [c for c in all_calls if c.status == 'completed' and c.duration]
                total_duration = sum(int(c.duration) for c in completed_calls_with_duration)
                average_duration_seconds = round((total_duration / len(completed_calls_with_duration)) if completed_calls_with_duration else 0, 0)
                
                # Format average duration
                avg_minutes = int(average_duration_seconds) // 60
                avg_seconds = int(average_duration_seconds) % 60
                average_call_duration = f"{avg_minutes}:{avg_seconds:02d}"
                
                # Find last call time
                last_call_time = None
                last_call_relative = "Never"
                
                if all_calls:
                    # Sort calls by date_created to get the most recent
                    sorted_calls = sorted(all_calls, key=lambda x: x.date_created if x.date_created else datetime.min, reverse=True)
                    if sorted_calls and sorted_calls[0].date_created:
                        last_call_time = sorted_calls[0].date_created
                        last_call_relative = get_relative_time(last_call_time)
                
                # Format created_at
                created_at_formatted = ""
                if created_at:
                    if hasattr(created_at, 'strftime'):
                        created_at_formatted = created_at.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        created_at_formatted = str(created_at)
                
                individual_result = {
                    "agent_name": agent_name,
                    "agent_type": agent_type,
                    "phone_number": phone_number,
                    "total_calls": total_calls,
                    "success_rate": f"{success_rate}%",
                    "success_rate_value": success_rate,
                    "average_call_duration": average_call_duration,
                    "average_call_duration_seconds": average_duration_seconds,
                    "created_at": created_at_formatted,
                    "last_call_time": last_call_time.isoformat() if last_call_time else None,
                    "last_call_relative": last_call_relative,
                    "status": "active" if total_calls > 0 else "inactive"
                }
                
                individual_results.append(individual_result)
                
            except Exception as e:
                # If one number fails, include error but continue with others
                agent_info = agent_phone_mapping.get(phone_number, {})
                individual_results.append({
                    "agent_name": agent_info.get("agent_name", f"Agent {phone_number[-4:]}"),
                    "agent_type": agent_info.get("agent_type", "Unknown"),
                    "phone_number": phone_number,
                    "total_calls": 0,
                    "success_rate": "0%",
                    "success_rate_value": 0,
                    "average_call_duration": "0:00",
                    "average_call_duration_seconds": 0,
                    "created_at": agent_info.get("created_at", "").strftime("%Y-%m-%d %H:%M:%S") if agent_info.get("created_at") else "",
                    "last_call_time": None,
                    "last_call_relative": "Never",
                    "status": "error",
                    "error": str(e)
                })
        
        # Sort by total calls (descending)
        individual_results.sort(key=lambda x: x["total_calls"], reverse=True)
        
        # Calculate summary statistics
        total_agents = len(individual_results)
        active_agents = len([r for r in individual_results if r["total_calls"] > 0])
        total_calls_all = sum(r["total_calls"] for r in individual_results)
        
        # Calculate overall success rate and fallback rate
        total_successful_calls = sum(
            int(r["success_rate_value"] * r["total_calls"] / 100) 
            for r in individual_results if r["total_calls"] > 0
        )
        total_failed_calls = total_calls_all - total_successful_calls
        
        overall_success_rate = round((total_successful_calls / total_calls_all * 100) if total_calls_all > 0 else 0, 1)
        overall_fallback_rate = round((total_failed_calls / total_calls_all * 100) if total_calls_all > 0 else 0, 1)
        
        return {
            "summary": {
                "total_agents": total_agents,
                "active_agents": active_agents,
                "inactive_agents": total_agents - active_agents,
                "total_calls_all_agents": total_calls_all,
                "success_rate": f"{overall_success_rate}%",
                "success_rate_value": overall_success_rate,
                "fallback_rate": f"{overall_fallback_rate}%",
                "fallback_rate_value": overall_fallback_rate,
                "data_period_days": 30
            },
            "individual_results": individual_results
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


@router.post("/analytics/agent-overview-analytics")
async def get_agent_overview_analytics(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get overview analytics with total calls, active agent count, success rate, 
    fallback rate, individual agents data, and recent activity.
    """
    try:
        client = get_twilio_client()
        
        # Get user's agents from database
        user_agents = get_agents_by_user_id(current_user.id)
        if not user_agents:
            raise HTTPException(
                status_code=404,
                detail="No agents found for current user"
            )
        
        # Extract phone numbers and create agent mapping
        phone_numbers = []
        agent_phone_mapping = {}
        
        for agent in user_agents:
            if agent.twilio_number:
                phone_numbers.append(agent.twilio_number)
                agent_phone_mapping[agent.twilio_number] = {
                    "agent_type": agent.agent_type or "Unknown",
                    "agent_name": agent.agent_name,
                    "agent_id": agent.agent_id,
                    "created_at": agent.created_at
                }
        
        if not phone_numbers:
            raise HTTPException(
                status_code=404,
                detail="No phone numbers found to analyze"
            )
        
        # Helper function to calculate relative time
        def get_relative_time(timestamp):
            if not timestamp:
                return "Never"
            
            now = datetime.now()
            # Handle timezone-aware timestamps
            if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            
            diff = now - timestamp
            
            if diff.days > 0:
                if diff.days == 1:
                    return "1 day ago"
                return f"{diff.days} days ago"
            elif diff.seconds >= 3600:
                hours = diff.seconds // 3600
                if hours == 1:
                    return "1 hour ago"
                return f"{hours} hours ago"
            elif diff.seconds >= 60:
                minutes = diff.seconds // 60
                if minutes == 1:
                    return "1 minute ago"
                return f"{minutes} minutes ago"
            else:
                return "Just now"
        
        # Initialize counters
        total_calls_all = 0
        total_successful_calls = 0
        total_failed_calls = 0
        active_agents_count = 0
        your_agents = []
        recent_activity = []
        
        # Get current date for analysis (last 30 days)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        # Process each phone number
        for phone_number in phone_numbers:
            try:
                # Normalize phone number format
                normalized_phone = phone_number
                if not normalized_phone.startswith('+'):
                    normalized_phone = '+' + normalized_phone
                
                # Get calls for this number (last 30 days)
                outgoing_calls = client.calls.list(
                    from_=normalized_phone,
                    start_time_after=start_date,
                    limit=1000
                )
                
                incoming_calls = client.calls.list(
                    to=normalized_phone,
                    start_time_after=start_date,
                    limit=1000
                )
                
                all_calls = list(outgoing_calls) + list(incoming_calls)
                
                # Get agent information
                agent_info = agent_phone_mapping.get(phone_number, {})
                agent_name = agent_info.get("agent_name", f"Agent {phone_number[-4:]}")
                agent_type = agent_info.get("agent_type", "Unknown")
                
                # Calculate statistics for this agent
                agent_total_calls = len(all_calls)
                agent_successful_calls = len([c for c in all_calls if c.status == 'completed'])
                agent_failed_calls = len([c for c in all_calls if c.status in ['failed', 'busy', 'no-answer', 'canceled']])
                agent_success_rate = round((agent_successful_calls / agent_total_calls * 100) if agent_total_calls > 0 else 0, 1)
                
                # Add to overall counters
                total_calls_all += agent_total_calls
                total_successful_calls += agent_successful_calls
                total_failed_calls += agent_failed_calls
                
                # Count as active if has calls
                if agent_total_calls > 0:
                    active_agents_count += 1
                
                # Add to your_agents list
                your_agents.append({
                    "agent_name": agent_name,
                    "agent_type": agent_type,
                    "total_calls": agent_total_calls,
                    "success_rate": f"{agent_success_rate}%",
                    "success_rate_value": agent_success_rate
                })
                
                # Find last call for recent activity
                if all_calls:
                    # Sort calls by date_created to get the most recent
                    sorted_calls = sorted(all_calls, key=lambda x: x.date_created if x.date_created else datetime.min, reverse=True)
                    if sorted_calls and sorted_calls[0].date_created:
                        last_call_time = sorted_calls[0].date_created
                        last_call_relative = get_relative_time(last_call_time)
                        
                        recent_activity.append({
                            "agent_type": agent_type,
                            "agent_name": agent_name,
                            "last_call_ago": last_call_relative
                        })
                else:
                    # Add agents with no calls to recent activity
                    recent_activity.append({
                        "agent_type": agent_type,
                        "agent_name": agent_name,
                        "last_call_ago": "Never"
                    })
                
            except Exception as e:
                print(f"Error processing phone number {phone_number}: {str(e)}")
                # Still add the agent to lists even if there's an error
                agent_info = agent_phone_mapping.get(phone_number, {})
                agent_name = agent_info.get("agent_name", f"Agent {phone_number[-4:]}")
                agent_type = agent_info.get("agent_type", "Unknown")
                
                your_agents.append({
                    "agent_name": agent_name,
                    "agent_type": agent_type,
                    "total_calls": 0,
                    "success_rate": "0%",
                    "success_rate_value": 0
                })
                
                recent_activity.append({
                    "agent_type": agent_type,
                    "agent_name": agent_name,
                    "last_call_ago": "Error"
                })
                continue
        
        # Calculate overall rates
        success_rate = round((total_successful_calls / total_calls_all * 100) if total_calls_all > 0 else 0, 1)
        fallback_rate = round((total_failed_calls / total_calls_all * 100) if total_calls_all > 0 else 0, 1)
        
        # Sort your_agents by total_calls (descending)
        your_agents.sort(key=lambda x: x["total_calls"], reverse=True)
        
        # Sort recent_activity by last activity (most recent first)
        def sort_by_activity(item):
            if item["last_call_ago"] == "Never":
                return 0  # Put "Never" at the end
            elif item["last_call_ago"] == "Error":
                return -1  # Put "Error" at the very end
            elif "Just now" in item["last_call_ago"]:
                return 1000  # Most recent
            elif "minute" in item["last_call_ago"]:
                return 500
            elif "hour" in item["last_call_ago"]:
                return 100
            elif "day" in item["last_call_ago"]:
                return 10
            else:
                return 1
        
        recent_activity.sort(key=sort_by_activity, reverse=True)
        
        return {
            "user_name": current_user.name,
            "total_calls": total_calls_all,
            "active_agent_count": active_agents_count,
            "success_rate": f"{success_rate}%",
            "success_rate_value": success_rate,
            "fallback_rate": f"{fallback_rate}%",
            "fallback_rate_value": fallback_rate,
            "your_agents": your_agents,
            "recent_activity": recent_activity
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


@router.post("/call_log/twilio-multiple-numbers-analytics")
async def get_multiple_numbers_analytics(
    request: MultiplePhoneNumbersRequest = MultiplePhoneNumbersRequest(),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get analytics for multiple phone numbers at once to save time.
    If no phone numbers provided, automatically fetches user's assigned agent numbers.
    Returns individual analytics for each number plus combined summary.
    """
    try:
        client = get_twilio_client()
        
        # If no phone numbers provided, get them from user's agents
        phone_numbers_to_process = request.phone_numbers
        agent_phone_mapping = {}  # Map phone numbers to agent info
        
        if not phone_numbers_to_process:
            # Get user's agents from database
            user_agents = get_agents_by_user_id(current_user.id)
            if not user_agents:
                raise HTTPException(
                    status_code=404,
                    detail="No agents found for current user"
                )
            
            phone_numbers_to_process = []
            for agent in user_agents:
                if agent.twilio_number:
                    phone_numbers_to_process.append(agent.twilio_number)
                    agent_phone_mapping[agent.twilio_number] = {
                        "agent_type": agent.agent_type,
                        "agent_name": agent.agent_name,
                        "agent_id": agent.agent_id
                    }
        
        if not phone_numbers_to_process:
            raise HTTPException(
                status_code=404,
                detail="No phone numbers found to analyze"
            )
        
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
        
        for phone_number in phone_numbers_to_process:
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
                
                # Add agent information if available
                if phone_number in agent_phone_mapping:
                    agent_info = agent_phone_mapping[phone_number]
                    individual_result["agent_type"] = agent_info.get("agent_type")
                    individual_result["agent_name"] = agent_info.get("agent_name")
                    individual_result["agent_id"] = agent_info.get("agent_id")
                
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
                error_result = {
                    "phone_number": phone_number,
                    "status": "error",
                    "error": str(e),
                    "call_statistics": None,
                    "message_statistics": None
                }
                
                # Add agent information if available
                if phone_number in agent_phone_mapping:
                    agent_info = agent_phone_mapping[phone_number]
                    error_result["agent_type"] = agent_info.get("agent_type")
                    error_result["agent_name"] = agent_info.get("agent_name")
                    error_result["agent_id"] = agent_info.get("agent_id")
                
                results.append(error_result)
        
        # Calculate combined summary
        combined_success_rate = (combined_stats["successful_calls"] / combined_stats["total_calls"] * 100) if combined_stats["total_calls"] > 0 else 0
        combined_failure_rate = (combined_stats["failed_calls"] / combined_stats["total_calls"] * 100) if combined_stats["total_calls"] > 0 else 0
        combined_avg_duration = combined_stats["total_duration"] / combined_stats["successful_calls"] if combined_stats["successful_calls"] > 0 else 0
        
        return {
            "request_summary": {
                "total_numbers_requested": len(phone_numbers_to_process),
                "successful_numbers": len([r for r in results if r["status"] == "success"]),
                "failed_numbers": len([r for r in results if r["status"] == "error"]),
                "include_recent_calls": request.include_recent_calls,
                "include_recent_messages": request.include_recent_messages,
                "auto_fetched_from_user_agents": not bool(request.phone_numbers)
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
