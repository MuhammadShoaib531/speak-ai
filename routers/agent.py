from fastapi import APIRouter, Depends, HTTPException, status
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import os
import shutil
import base64
import requests
import csv
import io
import pandas as pd
from datetime import datetime
from dateutil import parser as date_parser
import pytz
from typing import List, Dict, Optional
from pydantic import EmailStr, BaseModel
from fastapi import Form, File, UploadFile
from twilio.rest import Client
from database import get_db
from models import Agent, User
from auth import get_current_active_user

router = APIRouter(
    prefix="/auth/agent",
    tags=["Agent"]
)
BASE_URL = "https://api.elevenlabs.io/v1"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/convai/agents/create"
HEADERS = {
        "xi-api-key": ELEVENLABS_API_KEY
    }


def parse_human_datetime(datetime_str: str) -> int:
    """
    Parse human-readable datetime string to Unix timestamp.
    
    Supports various formats:
    - "2025-12-21 2 PM" or "2025-12-21 14:00"
    - "Dec 21, 2025 2:00 PM" 
    - "December 21, 2025 14:00"
    - "2025/12/21 2:00 PM"
    - "21-12-2025 14:00"
    - "2025-12-21T14:00:00" (ISO format)
    
    Returns Unix timestamp
    """
    try:
        datetime_str = datetime_str.strip()
        
        # Try dateutil parser first if available
        try:
            dt = date_parser.parse(datetime_str)
            # Convert to Unix timestamp
            return int(dt.timestamp())
        except (ImportError, NameError):
            # Fallback to manual parsing if dateutil is not available
            pass
        except:
            # Continue to manual parsing if dateutil fails
            pass
        
        # Manual parsing for common formats
        
        # Handle ISO format: "2025-12-21T14:00:00"
        if "T" in datetime_str:
            dt = datetime.fromisoformat(datetime_str.replace("T", " "))
            return int(dt.timestamp())
        
        # Handle AM/PM formats: "2025-12-21 2 PM"
        if " PM" in datetime_str.upper() or " AM" in datetime_str.upper():
            # Remove extra spaces and normalize
            datetime_str = " ".join(datetime_str.split())
            if " PM" in datetime_str.upper():
                datetime_str = datetime_str.upper().replace(" PM", " PM")
                dt = datetime.strptime(datetime_str, "%Y-%m-%d %I %p")
            else:
                datetime_str = datetime_str.upper().replace(" AM", " AM")
                dt = datetime.strptime(datetime_str, "%Y-%m-%d %I %p")
            return int(dt.timestamp())
        
        # Handle 24-hour format: "2025-12-21 14:00"
        if ":" in datetime_str and "-" in datetime_str:
            # Try different separators
            for sep in ["-", "/"]:
                if sep in datetime_str:
                    try:
                        dt = datetime.strptime(datetime_str, f"%Y{sep}%m{sep}%d %H:%M")
                        return int(dt.timestamp())
                    except:
                        try:
                            dt = datetime.strptime(datetime_str, f"%d{sep}%m{sep}%Y %H:%M")
                            return int(dt.timestamp())
                        except:
                            continue
        
        # Handle date only formats by adding default time (12:00 PM)
        if datetime_str.count("-") == 2 and ":" not in datetime_str:
            datetime_str += " 12:00"
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
            return int(dt.timestamp())
        
        raise ValueError(f"Unsupported datetime format: {datetime_str}")
        
    except Exception as e:
        raise ValueError(f"Invalid datetime format '{datetime_str}'. Supported formats: '2025-12-21 2 PM', '2025-12-21 14:00', '2025-12-21T14:00:00', etc. Error: {str(e)}")


def parse_human_datetime_simple(datetime_str: str) -> int:
    """
    Simple datetime parser that doesn't require external libraries.
    Supports basic formats without advanced parsing.
    """
    try:
        datetime_str = datetime_str.strip()
        
        # Handle ISO format: "2025-12-21T14:00:00"
        if "T" in datetime_str:
            dt = datetime.fromisoformat(datetime_str.replace("T", " "))
            return int(dt.timestamp())
        
        # Handle AM/PM formats: "2025-12-21 2 PM"
        if " PM" in datetime_str.upper():
            datetime_str = datetime_str.upper().replace(" PM", " PM")
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %I %p")
            return int(dt.timestamp())
        elif " AM" in datetime_str.upper():
            datetime_str = datetime_str.upper().replace(" AM", " AM")  
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %I %p")
            return int(dt.timestamp())
        
        # Handle 24-hour format: "2025-12-21 14:00"
        if ":" in datetime_str and "-" in datetime_str:
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
            return int(dt.timestamp())
        
        # Handle date only by adding 12:00
        if datetime_str.count("-") == 2:
            datetime_str += " 12:00"
            dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
            return int(dt.timestamp())
        
        raise ValueError(f"Unsupported format: {datetime_str}")
        
    except Exception as e:
        raise ValueError(f"Invalid datetime format '{datetime_str}'. Use formats like: '2025-12-21 2 PM', '2025-12-21 14:00', '2025-12-21T14:00:00'. Error: {str(e)}")


def upload_to_s3(file_path: str, s3_key: str) -> str:
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )
    bucket_name = os.getenv("AWS_S3_BUCKET")

    try:
        s3.upload_file(file_path, bucket_name, s3_key)
        return f"https://{bucket_name}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{s3_key}"
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to S3: {str(e)}")

def buy_twilio_number(agent_name: str):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    client = Client(account_sid, auth_token)

    # Search for available US phone numbers (you can change country, type, etc.)
    available_numbers = client.available_phone_numbers("US").local.list(limit=1)

    if not available_numbers:
        raise Exception("No phone numbers available for purchase.")

    phone_number = available_numbers[0].phone_number

    # Purchase the number
    purchased = client.incoming_phone_numbers.create(
        phone_number=phone_number,
        friendly_name=f"{agent_name} Line"
    )

    return {
        "twilio_number": purchased.phone_number,
        "sid": purchased.sid
    }

@router.post("/create-agent")
async def create_agent(
    agent_name: str = Form(...),
    first_message: str = Form(...),
    prompt: str = Form(...),
    email: EmailStr = Form(...),
    llm: str = Form(...),
    file: UploadFile = File(None),
    voice_file: UploadFile = File(None),
    business_name: str = Form(None),
    agent_type: str = Form(None),
    speaking_style: str = Form(None),
):

    documentation_id = None
    file_name = None
    voice_id = None
    file_url = None

    if file is not None:
        # Validate file type - only allow PDF and DOCX
        allowed_extensions = ['.pdf', '.docx']
        file_extension = os.path.splitext(file.filename)[1].lower()
        
        if file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type. Only PDF and DOCX files are allowed. Received: {file_extension}"
            )
        
        # Validate file content type
        allowed_content_types = [
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ]
        
        if file.content_type not in allowed_content_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid content type. Only PDF and DOCX files are allowed. Received: {file.content_type}"
            )
        
        os.makedirs("uploads", exist_ok=True)
        file_path = os.path.join("uploads", file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_name = file.filename
        s3_key = f"user_docs/{email}/{file.filename}"
        file_url = upload_to_s3(file_path, s3_key)

        encoded_file = base64.b64encode(open(file_path, "rb").read()).decode("utf-8")
        
        # Determine file type for ElevenLabs API
        if file_extension == '.pdf':
            files = {'file': (file.filename, base64.b64decode(encoded_file), 'application/pdf')}
        elif file_extension == '.docx':
            files = {'file': (file.filename, base64.b64decode(encoded_file), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}

        kb_response = requests.post(
            f"{BASE_URL}/convai/knowledge-base",
            headers=HEADERS,
            files=files,
            timeout=30
        )
        if kb_response.status_code != 200:
            raise HTTPException(status_code=kb_response.status_code, detail=f"KB creation failed: {kb_response.text}")

        documentation_id = kb_response.json().get("id")

        rag_payload = {
            "text": True,
            "chunk_size": 256,
            "chunk_overlap": 0,
            "model": "e5_mistral_7b_instruct"
        }

        rag_response = requests.post(
            f"{BASE_URL}/convai/knowledge-base/{documentation_id}/rag-index",
            headers={**HEADERS, "Content-Type": "application/json"},
            json=rag_payload,
            timeout=30
        )
        if rag_response.status_code != 200:
            raise HTTPException(status_code=rag_response.status_code,
                                detail=f"RAG indexing failed: {rag_response.text}")
    voice_url = None
    if voice_file:
        try:
            # Validate voice file type - only allow common audio formats
            allowed_voice_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac']
            voice_file_extension = os.path.splitext(voice_file.filename)[1].lower()
            
            if voice_file_extension not in allowed_voice_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid voice file type. Only audio files are allowed (.mp3, .wav, .m4a, .ogg, .flac, .aac). Received: {voice_file_extension}"
                )
            
            # Validate voice file content type
            allowed_voice_content_types = [
                'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/wave', 'audio/x-wav',
                'audio/mp4', 'audio/m4a', 'audio/ogg', 'audio/flac', 'audio/aac'
            ]
            
            if voice_file.content_type not in allowed_voice_content_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid voice file content type. Only audio files are allowed. Received: {voice_file.content_type}"
                )
            
            # Save locally first
            os.makedirs("uploads", exist_ok=True)
            voice_path = os.path.join("uploads", voice_file.filename)
            with open(voice_path, "wb") as buffer:
                shutil.copyfileobj(voice_file.file, buffer)

            # Upload to S3
            s3_key_voice = f"user_voices/{email}/{voice_file.filename}"
            voice_url = upload_to_s3(voice_path, s3_key_voice)

            # Then send to ElevenLabs API
            elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
            voice_upload_url = "https://api.elevenlabs.io/v1/voices/add"

            voice_data = {
                "name": f"{agent_name}_voice",
                "description": f"Voice clone for agent {agent_name}",
                "labels": '{"user_uploaded": "true"}'
            }

            with open(voice_path, "rb") as f:
                voice_files = {
                    "files": (voice_file.filename, f, voice_file.content_type)
                }

                headers = {
                    "xi-api-key": elevenlabs_api_key
                }

                response = requests.post(
                    voice_upload_url,
                    data=voice_data,
                    files=voice_files,
                    headers=headers
                )

            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code,
                                    detail=f"Voice cloning failed: {response.text}")

            voice_id = response.json().get("voice_id")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Voice cloning error: {str(e)}")
    else:
        voice_id = "IKne3meq5aSn9XLyUdCD"
        voice_url= "Not Upload file"

    prompt_block = {
        "prompt": prompt,
        "llm": llm
    }

    if documentation_id:
        prompt_block["knowledge_base"] = [{
            "id": documentation_id,
            "type": "file",
            "name": file_name or "uploaded-doc"
        }]
    try:
        twilio_info = buy_twilio_number(agent_name)
        twilio_number = twilio_info["twilio_number"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Twilio number provisioning failed: {str(e)}")
        
    agent_payload = {
        "name": agent_name,
        "conversation_config": {
            "conversation": {
                "client_events": [
                    "agent_response", "interruption", "user_transcript",
                    "agent_response_correction", "audio"
                ]
            },
            "agent": {
                "first_message": first_message,
                "language": "en",
                "prompt": prompt_block,
                "voice": {
                    "voice_id": voice_id
                }
            }
        }
    }
    agent_response = requests.post(
        f"{BASE_URL}/convai/agents/create",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=agent_payload,
        timeout=30
    )

    if agent_response.status_code != 200:
        raise HTTPException(status_code=agent_response.status_code,
                            detail=f"Agent creation failed: {agent_response.text}")
    response = requests.post("https://api.elevenlabs.io/v1/convai/phone-numbers",
     headers={
    "xi-api-key": ELEVENLABS_API_KEY},
    json={
    "phone_number": twilio_number,
    "label": agent_name,
    "sid": os.getenv("TWILIO_ACCOUNT_SID"),
    "token": os.getenv("TWILIO_AUTH_TOKEN"),
    "supports_inbound": True,
    "supports_outbound": True
    },
     )
    response_data = response.json()
    phone_number_id = response_data.get("phone_number_id")
    print(phone_number_id)
    agent_id = agent_response.json().get("agent_id") or agent_response.json().get("id")

    response = requests.patch(f"https://api.elevenlabs.io/v1/convai/phone-numbers/{phone_number_id}",
     headers={
    "xi-api-key": ELEVENLABS_API_KEY
    },
    json={
    "agent_id": agent_id
    },
    )
    if response.status_code == 200:
        print("✅ Phone number successfully linked to agent.")
    else:
        print(f"❌ Failed to update phone number. Status: {response.status_code}")
        print("Response:", response.text)

    response_data = {
        "status": "success",
        "agent_id": agent_id,
        "documentation_id": documentation_id,
        "file_name": file_name,
        "file_url": file_url,
        "voice_id": voice_id,
        "twilio_number": twilio_number,
        "phone_number_id": phone_number_id,
    }

    # Store agent data in database
    with get_db() as conn:
        cursor = conn.cursor()
        
        # First get the user_id from email
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user_result = cursor.fetchone()
        if not user_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found with provided email"
            )
        user_id = user_result[0]
        
        # Then insert the agent data including phone_number_id
        cursor.execute("""
            INSERT INTO agents (
                user_id, agent_id, agent_name, first_message, prompt, llm,
                documentation_id, file_name, file_url, voice_id, twilio_number,
                phone_number_id, business_name, agent_type, speaking_style
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) RETURNING id
        """, (
            user_id, agent_id, agent_name, first_message, prompt, llm,
            documentation_id, file_name, file_url, voice_id, twilio_number,
            phone_number_id, business_name, agent_type, speaking_style
        ))
        agent_db_id = cursor.fetchone()[0]
        conn.commit()
        response_data["db_id"] = agent_db_id

    return response_data


@router.put("/update-agent")
async def update_agent(
    email: EmailStr = Form(...),
    agent_name: str = Form(...),
    first_message: str = Form(None),
    prompt: str = Form(None),
    llm: str = Form(None),
    file: UploadFile = File(None),
    voice_file: UploadFile = File(None),
    business_name: str = Form(None),
    agent_type: str = Form(None),
    speaking_style: str = Form(None),
):
    # First, get the existing agent data from database
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get user_id from email
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user_result = cursor.fetchone()
        if not user_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found with provided email"
            )
        user_id = user_result[0]
        
        # Get existing agent data by matching agent_name and user_id
        cursor.execute("""
            SELECT agent_id, agent_name, first_message, prompt, llm, documentation_id, 
                   file_name, file_url, voice_id, phone_number_id, business_name, agent_type, speaking_style
            FROM agents 
            WHERE user_id = %s AND agent_name = %s
        """, (user_id, agent_name))
        
        existing_agent = cursor.fetchone()
        if not existing_agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No agent found with name '{agent_name}' for this user"
            )
        
        agent_id = existing_agent[0]
        
        # Use existing values if new ones aren't provided (agent_name stays the same)
        current_agent_name = existing_agent[1]  # Keep the existing agent_name
        current_first_message = first_message if first_message else existing_agent[2]
        current_prompt = prompt if prompt else existing_agent[3]
        current_llm = llm if llm else existing_agent[4]
        current_documentation_id = existing_agent[5]
        current_file_name = existing_agent[6]
        current_file_url = existing_agent[7]
        current_voice_id = existing_agent[8]
        current_phone_number_id = existing_agent[9]  # Don't allow updating phone_number_id
        current_business_name = business_name if business_name else existing_agent[10]
        current_agent_type = agent_type if agent_type else existing_agent[11]
        current_speaking_style = speaking_style if speaking_style else existing_agent[12]

    # Handle file upload if provided
    if file is not None:
        # Validate file type - only allow PDF and DOCX
        allowed_extensions = ['.pdf', '.docx']
        file_extension = os.path.splitext(file.filename)[1].lower()
        
        if file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type. Only PDF and DOCX files are allowed. Received: {file_extension}"
            )
        
        # Validate file content type
        allowed_content_types = [
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ]
        
        if file.content_type not in allowed_content_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid content type. Only PDF and DOCX files are allowed. Received: {file.content_type}"
            )
        
        os.makedirs("uploads", exist_ok=True)
        file_path = os.path.join("uploads", file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        current_file_name = file.filename
        s3_key = f"user_docs/{email}/{file.filename}"
        current_file_url = upload_to_s3(file_path, s3_key)

        encoded_file = base64.b64encode(open(file_path, "rb").read()).decode("utf-8")
        
        # Determine file type for ElevenLabs API
        if file_extension == '.pdf':
            files = {'file': (file.filename, base64.b64decode(encoded_file), 'application/pdf')}
        elif file_extension == '.docx':
            files = {'file': (file.filename, base64.b64decode(encoded_file), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}

        kb_response = requests.post(
            f"{BASE_URL}/convai/knowledge-base",
            headers=HEADERS,
            files=files,
            timeout=30
        )
        if kb_response.status_code != 200:
            raise HTTPException(status_code=kb_response.status_code, detail=f"KB creation failed: {kb_response.text}")

        current_documentation_id = kb_response.json().get("id")

        rag_payload = {
            "text": True,
            "chunk_size": 256,
            "chunk_overlap": 0,
            "model": "e5_mistral_7b_instruct"
        }

        rag_response = requests.post(
            f"{BASE_URL}/convai/knowledge-base/{current_documentation_id}/rag-index",
            headers={**HEADERS, "Content-Type": "application/json"},
            json=rag_payload,
            timeout=30
        )
        if rag_response.status_code != 200:
            raise HTTPException(status_code=rag_response.status_code,
                                detail=f"RAG indexing failed: {rag_response.text}")

    # Handle voice file upload if provided
    voice_url = None
    if voice_file:
        try:
            # Validate voice file type - only allow common audio formats
            allowed_voice_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac']
            voice_file_extension = os.path.splitext(voice_file.filename)[1].lower()
            
            if voice_file_extension not in allowed_voice_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid voice file type. Only audio files are allowed (.mp3, .wav, .m4a, .ogg, .flac, .aac). Received: {voice_file_extension}"
                )
            
            # Validate voice file content type
            allowed_voice_content_types = [
                'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/wave', 'audio/x-wav',
                'audio/mp4', 'audio/m4a', 'audio/ogg', 'audio/flac', 'audio/aac'
            ]
            
            if voice_file.content_type not in allowed_voice_content_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid voice file content type. Only audio files are allowed. Received: {voice_file.content_type}"
                )
            
            # Save locally first
            os.makedirs("uploads", exist_ok=True)
            voice_path = os.path.join("uploads", voice_file.filename)
            with open(voice_path, "wb") as buffer:
                shutil.copyfileobj(voice_file.file, buffer)

            # Upload to S3
            s3_key_voice = f"user_voices/{email}/{voice_file.filename}"
            voice_url = upload_to_s3(voice_path, s3_key_voice)

            # Then send to ElevenLabs API
            elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
            voice_upload_url = "https://api.elevenlabs.io/v1/voices/add"

            voice_data = {
                "name": f"{current_agent_name}_voice_updated",
                "description": f"Updated voice clone for agent {current_agent_name}",
                "labels": '{"user_uploaded": "true", "updated": "true"}'
            }

            with open(voice_path, "rb") as f:
                voice_files = {
                    "files": (voice_file.filename, f, voice_file.content_type)
                }

                headers = {
                    "xi-api-key": elevenlabs_api_key
                }

                response = requests.post(
                    voice_upload_url,
                    data=voice_data,
                    files=voice_files,
                    headers=headers
                )

            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code,
                                    detail=f"Voice cloning failed: {response.text}")

            current_voice_id = response.json().get("voice_id")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Voice cloning error: {str(e)}")

    # Prepare the prompt block
    prompt_block = {
        "prompt": current_prompt,
        "llm": current_llm
    }

    if current_documentation_id:
        prompt_block["knowledge_base"] = [{
            "id": current_documentation_id,
            "type": "file",
            "name": current_file_name or "uploaded-doc"
        }]

    # Update agent payload
    agent_payload = {
        "name": current_agent_name,
        "conversation_config": {
            "conversation": {
                "client_events": [
                    "agent_response", "interruption", "user_transcript",
                    "agent_response_correction", "audio"
                ]
            },
            "agent": {
                "first_message": current_first_message,
                "language": "en",
                "prompt": prompt_block,
                "voice": {
                    "voice_id": current_voice_id
                }
            }
        }
    }

    # Update agent via ElevenLabs API
    agent_response = requests.patch(
        f"{BASE_URL}/convai/agents/{agent_id}",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=agent_payload,
        timeout=30
    )

    if agent_response.status_code != 200:
        raise HTTPException(status_code=agent_response.status_code,
                            detail=f"Agent update failed: {agent_response.text}")

    # Update agent data in database
    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE agents SET 
                agent_name = %s, first_message = %s, prompt = %s, llm = %s,
                documentation_id = %s, file_name = %s, file_url = %s, voice_id = %s,
                phone_number_id = %s, business_name = %s, agent_type = %s, speaking_style = %s
            WHERE agent_id = %s AND user_id = %s
        """, (
            current_agent_name, current_first_message, current_prompt, current_llm,
            current_documentation_id, current_file_name, current_file_url, current_voice_id,
            current_phone_number_id, current_business_name, current_agent_type, current_speaking_style,
            agent_id, user_id
        ))
        
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found or update failed"
            )
        
        conn.commit()

    response_data = {
        "status": "success",
        "message": "Agent updated successfully",
        "agent_id": agent_id,
        "agent_name": current_agent_name,
        "documentation_id": current_documentation_id,
        "file_name": current_file_name,
        "file_url": current_file_url,
        "voice_id": current_voice_id,
    }

    return response_data


@router.delete("/delete-agent/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete an agent by agent_id. This will:
    1. Remove agent from ElevenLabs
    2. Release/delete Twilio phone number
    3. Remove agent from database
    Only the agent owner or super admin can delete agents.
    """
    try:
        # First, get the agent data from database to verify ownership
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the agent
            if current_user.role.lower() == "super admin":
                # Super admin can delete any agent
                cursor.execute("""
                    SELECT id, user_id, agent_name, twilio_number, voice_id, phone_number_id
                    FROM agents 
                    WHERE agent_id = %s
                """, (agent_id,))
            else:
                # Regular user can only delete their own agents
                cursor.execute("""
                    SELECT id, user_id, agent_name, twilio_number, voice_id, phone_number_id
                    FROM agents 
                    WHERE agent_id = %s AND user_id = %s
                """, (agent_id, current_user.id))
            
            agent_data = cursor.fetchone()
            if not agent_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found or you don't have permission to delete it"
                )
            
            db_id, user_id, agent_name, twilio_number, voice_id, phone_number_id = agent_data

        # Step 1: Delete agent from ElevenLabs
        try:
            agent_delete_response = requests.delete(
                f"{BASE_URL}/convai/agents/{agent_id}",
                headers=HEADERS,
                timeout=30
            )
            
            if agent_delete_response.status_code not in [200, 204, 404]:
                print(f"Warning: Failed to delete agent from ElevenLabs. Status: {agent_delete_response.status_code}")
                print(f"Response: {agent_delete_response.text}")
                # Continue with deletion even if ElevenLabs fails
                
        except Exception as e:
            print(f"Warning: Error deleting agent from ElevenLabs: {str(e)}")
            # Continue with deletion even if ElevenLabs fails

        # Step 2: Delete voice from ElevenLabs if it exists and was user uploaded
        if voice_id and voice_id != "IKne3meq5aSn9XLyUdCD":  # Don't delete default voice
            try:
                voice_delete_response = requests.delete(
                    f"{BASE_URL}/voices/{voice_id}",
                    headers=HEADERS,
                    timeout=30
                )
                
                if voice_delete_response.status_code not in [200, 204, 404]:
                    print(f"Warning: Failed to delete voice from ElevenLabs. Status: {voice_delete_response.status_code}")
                    
            except Exception as e:
                print(f"Warning: Error deleting voice from ElevenLabs: {str(e)}")

        # Step 3: Delete phone number from ElevenLabs using stored phone_number_id
        if phone_number_id:
            try:
                delete_phone_response = requests.delete(
                    f"https://api.elevenlabs.io/v1/convai/phone-numbers/{phone_number_id}",
                    headers={"xi-api-key": ELEVENLABS_API_KEY},
                    timeout=30
                )
                
                if delete_phone_response.status_code in [200, 204]:
                    print(f"✅ Successfully deleted phone number from ElevenLabs: {phone_number_id}")
                elif delete_phone_response.status_code == 404:
                    print(f"Warning: Phone number {phone_number_id} not found in ElevenLabs (already deleted)")
                else:
                    print(f"Warning: Failed to delete phone number from ElevenLabs. Status: {delete_phone_response.status_code}")
                    print(f"Response: {delete_phone_response.text}")
                    
            except Exception as e:
                print(f"Warning: Error deleting phone number from ElevenLabs: {str(e)}")
        else:
            print("Warning: No phone_number_id found in database for this agent")

        # Step 4: Release Twilio phone number
        try:
            account_sid = os.getenv("TWILIO_ACCOUNT_SID")
            auth_token = os.getenv("TWILIO_AUTH_TOKEN")
            client = Client(account_sid, auth_token)
            
            # Find the Twilio phone number SID
            incoming_numbers = client.incoming_phone_numbers.list()
            twilio_sid = None
            
            for number in incoming_numbers:
                if number.phone_number == twilio_number:
                    twilio_sid = number.sid
                    break
            
            # Delete the phone number from Twilio
            if twilio_sid:
                client.incoming_phone_numbers(twilio_sid).delete()
                print(f"✅ Successfully released Twilio number: {twilio_number}")
            else:
                print(f"Warning: Twilio number {twilio_number} not found in account")
                
        except Exception as e:
            print(f"Warning: Error releasing Twilio number: {str(e)}")
            # Continue with database deletion even if Twilio fails

        # Step 5: Delete agent from database
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM agents 
                WHERE agent_id = %s
            """, (agent_id,))
            
            if cursor.rowcount == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found in database"
                )
            
            conn.commit()

        return {
            "status": "success",
            "message": f"Agent '{agent_name}' deleted successfully",
            "deleted_agent": {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "twilio_number": twilio_number,
                "voice_id": voice_id
            },
            "actions_completed": {
                "elevenlabs_agent_deleted": True,
                "elevenlabs_voice_deleted": voice_id != "IKne3meq5aSn9XLyUdCD" if voice_id else False,
                "elevenlabs_phone_removed": True,
                "twilio_number_released": True,
                "database_record_deleted": True
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting agent: {str(e)}"
        )


@router.patch("/pause-twilio-number/{agent_id}")
async def pause_twilio_number(
    agent_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Pause a Twilio phone number associated with an agent.
    This removes the agent association from the ElevenLabs phone number.
    Only agent owner or super admin can pause numbers.
    """
    try:
        # Get agent data from database to verify ownership and get phone number
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the agent
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT agent_name, phone_number_id, twilio_number, user_id
                    FROM agents 
                    WHERE agent_id = %s
                """, (agent_id,))
            else:
                cursor.execute("""
                    SELECT agent_name, phone_number_id, twilio_number, user_id
                    FROM agents 
                    WHERE agent_id = %s AND user_id = %s
                """, (agent_id, current_user.id))
            
            agent_data = cursor.fetchone()
            if not agent_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found or you don't have permission to modify it"
                )
            
            agent_name, phone_number_id, twilio_number, user_id = agent_data

        if not phone_number_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No phone number ID found for this agent"
            )

        print(f"Pausing phone number: {phone_number_id}")

        # Remove agent association from ElevenLabs phone number (pause it)
        response = requests.patch(f"https://api.elevenlabs.io/v1/convai/phone-numbers/{phone_number_id}",
         headers={
        "xi-api-key": ELEVENLABS_API_KEY
        },
        json={
        "agent_id": None  # Remove agent association to pause
        },
        )
        
        if response.status_code == 200:
            print("✅ Phone number successfully paused (agent unlinked).")
            return {
                "status": "success",
                "message": f"Twilio number {twilio_number} has been paused",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "twilio_number": twilio_number,
                "phone_number_id": phone_number_id,
                "current_status": "paused",
                "updated_by": current_user.name
            }
        else:
            print(f"❌ Failed to pause phone number. Status: {response.status_code}")
            print("Response:", response.text)
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to pause phone number: {response.text}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error pausing phone number: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error pausing phone number: {str(e)}"
        )


@router.patch("/resume-twilio-number/{agent_id}")
async def resume_twilio_number(
    agent_id: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Resume a paused Twilio phone number associated with an agent.
    This re-enables incoming calls by restoring the ElevenLabs webhook URL.
    Only agent owner or super admin can resume numbers.
    """
    try:
        # Get agent data from database
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the agent
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT agent_name, phone_number_id, twilio_number, user_id
                    FROM agents 
                    WHERE agent_id = %s
                """, (agent_id,))
            else:
                cursor.execute("""
                    SELECT agent_name, phone_number_id, twilio_number, user_id
                    FROM agents 
                    WHERE agent_id = %s AND user_id = %s
                """, (agent_id, current_user.id))
            
            agent_data = cursor.fetchone()
            if not agent_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found or you don't have permission to modify it"
                )
            
            agent_name, phone_number_id, twilio_number, user_id = agent_data
        print(phone_number_id)

        response = requests.patch(f"https://api.elevenlabs.io/v1/convai/phone-numbers/{phone_number_id}",
         headers={
        "xi-api-key": ELEVENLABS_API_KEY
        },
        json={
        "agent_id": agent_id
        },
        )
        if response.status_code == 200:
            print("✅ Phone number successfully linked to agent.")
        else:
            print(f"❌ Failed to update phone number. Status: {response.status_code}")
            print("Response:", response.text)
        return {
            "status": "success",
            "message": f"Twilio number {twilio_number} has been resumed",
            "agent_id": agent_id,
            "agent_name": agent_name,
            "twilio_number": twilio_number,
            "phone_number_id": phone_number_id,
            "updated_by": current_user.name
        }
    except Exception as e:
        print(f"❌ Error linking phone number: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error linking phone number: {str(e)}"
        )


# Pydantic models for batch calling
class BatchCallRecipient(BaseModel):
    phone_number: str

class BatchCallResult(BaseModel):
    phone_number: str
    status: str
    call_id: Optional[str] = None
    error: Optional[str] = None

class BatchCallResponse(BaseModel):
    status: str
    message: str
    agent_id: str
    agent_name: str
    batch_job_id: Optional[str] = None
    call_name: str
    total_numbers: int
    scheduled_time: Optional[str] = None
    recipients: List[BatchCallRecipient]


@router.post("/batch-calling", response_model=BatchCallResponse)
async def batch_calling(
    agent_name: str = Form(...),
    csv_file: UploadFile = File(...),
    phone_column: str = Form("phone"),  # Default column name for phone numbers
    call_name: str = Form(...),  # Name for the batch calling job
    scheduled_time: Optional[str] = Form(None),  # Optional scheduled time (human-readable format)
    current_user: User = Depends(get_current_active_user)
):
    """
    Perform batch calling using ElevenLabs batch calling API with a CSV or Excel file containing phone numbers.
    
    Args:
        agent_name: The name of the agent to use for calling
        csv_file: CSV (.csv) or Excel (.xlsx) file containing phone numbers
        phone_column: Name of the column containing phone numbers (default: 'phone')
        call_name: Name for the batch calling job
        scheduled_time: Optional human-readable scheduled time (e.g., "2025-12-21 2 PM", "2025-12-21 14:00", "Dec 21, 2025 2:00 PM")
        
    Supported file formats:
        - CSV (.csv)
        - Excel (.xlsx)
        
    Supported date/time formats:
        - "2025-12-21 2 PM" or "2025-12-21 14:00"
        - "Dec 21, 2025 2:00 PM" 
        - "December 21, 2025 14:00"
        - "2025/12/21 2:00 PM"
        - "21-12-2025 14:00"
        - "2025-12-21T14:00:00" (ISO format)
        
    Returns:
        BatchCallResponse with batch job details
    """
    try:
        # Validate file type - allow both CSV and Excel files
        allowed_extensions = ['.csv', '.xlsx']
        file_extension = None
        for ext in allowed_extensions:
            if csv_file.filename.endswith(ext):
                file_extension = ext
                break
        
        if not file_extension:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only CSV and Excel (.xlsx) files are allowed"
            )
        
        # Validate content type
        if file_extension == '.csv':
            allowed_content_types = ['text/csv', 'application/csv', 'application/vnd.ms-excel']
        else:  # .xlsx
            allowed_content_types = [
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'application/vnd.ms-excel'
            ]
        
        if csv_file.content_type not in allowed_content_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid content type. Expected {file_extension} file. Received: {csv_file.content_type}"
            )
        
        # Get agent data from database to verify ownership and get phone number
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the agent
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT agent_id, agent_name, phone_number_id, twilio_number, user_id
                    FROM agents 
                    WHERE agent_name = %s
                """, (agent_name,))
            else:
                cursor.execute("""
                    SELECT agent_id, agent_name, phone_number_id, twilio_number, user_id
                    FROM agents 
                    WHERE agent_name = %s AND user_id = %s
                """, (agent_name, current_user.id))
            
            agent_data = cursor.fetchone()
            if not agent_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found or you don't have permission to use it"
                )
            
            agent_id, agent_name, phone_number_id, twilio_number, user_id = agent_data

        if not phone_number_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent doesn't have a phone number configured"
            )

        # Read and parse file (CSV or Excel)
        if file_extension == '.csv':
            # Handle CSV files
            csv_content = await csv_file.read()
            csv_string = csv_content.decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(csv_string))
            rows = list(csv_reader)
        else:  # .xlsx
            # Handle Excel files
            try:
                file_content = await csv_file.read()
                
                # Save temporarily to read with pandas
                temp_file_path = f"temp_{csv_file.filename}"
                with open(temp_file_path, "wb") as temp_file:
                    temp_file.write(file_content)
                
                try:
                    # Read Excel file
                    df = pd.read_excel(temp_file_path)
                    # Handle NaN values by replacing them with empty strings
                    df = df.fillna('')
                    # Convert to list of dictionaries (same format as CSV reader)
                    rows = df.to_dict('records')
                finally:
                    # Clean up temporary file
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
            except ImportError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Excel file support not available. Please install pandas and openpyxl."
                )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Error reading Excel file: {str(e)}"
                )
        
        # Extract phone numbers from rows
        phone_numbers = []
        row_count = 0
        
        for row in rows:
            row_count += 1
            if phone_column not in row:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Column '{phone_column}' not found in file. Available columns: {list(row.keys())}"
                )
            
            # Convert to string first (important for Excel files where numbers might be integers)
            phone_number = str(row[phone_column]).strip() if row[phone_column] is not None else ""
            if phone_number and phone_number.lower() not in ['nan', 'none', '']:  # Only add non-empty phone numbers
                # Basic phone number validation (remove spaces, dashes, etc.)
                cleaned_phone = ''.join(filter(str.isdigit, phone_number))
                if len(cleaned_phone) >= 10:  # Minimum valid phone number length
                    # Add country code if not present
                    if not cleaned_phone.startswith('1') and len(cleaned_phone) == 10:
                        cleaned_phone = '1' + cleaned_phone
                    phone_numbers.append(f"+{cleaned_phone}")
                else:
                    print(f"Skipping invalid phone number: {phone_number}")
        
        if not phone_numbers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid phone numbers found in the uploaded file"
            )
        
        print(f"Found {len(phone_numbers)} valid phone numbers for batch calling")
        
        # Prepare recipients for ElevenLabs batch calling API
        recipients = [{"phone_number": phone} for phone in phone_numbers]

        scheduled_time_unix= 42
        # Handle scheduled time - prioritize human-readable format over Unix timestamp
        final_scheduled_time_unix = None
        
        if scheduled_time:
            # Parse human-readable datetime
            try:
                # Try advanced parser first, fallback to simple parser
                try:
                    final_scheduled_time_unix = parse_human_datetime(scheduled_time)
                except (ImportError, NameError):
                    # Use simple parser if dateutil/pytz not available
                    final_scheduled_time_unix = parse_human_datetime_simple(scheduled_time)
                print(f"Parsed scheduled time '{scheduled_time}' to Unix timestamp: {final_scheduled_time_unix}")
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e)
                )
        elif scheduled_time_unix:
            # Use provided Unix timestamp (backward compatibility)
            final_scheduled_time_unix = scheduled_time_unix
        
        # Prepare the batch calling payload
        batch_payload = {
            "call_name": call_name,
            "agent_id": agent_id,
            "agent_phone_number_id": phone_number_id,
            "recipients": recipients
        }
        
        # Add scheduled time if provided
        if final_scheduled_time_unix:
            batch_payload["scheduled_time_unix"] = final_scheduled_time_unix
        else:
            batch_payload["scheduled_time_unix"] = 42
        print(batch_payload)
        # Submit batch calling job to ElevenLabs
        batch_response = requests.post(
            "https://api.elevenlabs.io/v1/convai/batch-calling/submit",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json"
            },
            json=batch_payload,
            timeout=30
        )
        
        if batch_response.status_code != 200:
            raise HTTPException(
                status_code=batch_response.status_code,
                detail=f"ElevenLabs batch calling failed: {batch_response.text}"
            )
        
        batch_result = batch_response.json()
        batch_job_id = batch_result.get("batch_id") or batch_result.get("id")
        
        print(f"✅ Batch calling job submitted successfully. Job ID: {batch_job_id}")
        
        # Store batch call record in database
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Insert batch calling record for tracking
            cursor.execute("""
                INSERT INTO batch_calls (
                    user_id, agent_id, batch_job_id, call_name, total_numbers,
                    scheduled_time_unix, status, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, NOW()
                )
            """, (
                user_id, agent_id, batch_job_id, call_name, len(phone_numbers),
                final_scheduled_time_unix, "submitted"
            ))
            conn.commit()
            print(f"Batch calling record saved to database")
        
        # Format response
        scheduled_time_str = None
        if final_scheduled_time_unix:
            scheduled_time_str = datetime.fromtimestamp(final_scheduled_time_unix).isoformat()
        
        return BatchCallResponse(
            status="success",
            message=f"Batch calling job submitted successfully. {len(phone_numbers)} numbers queued for calling.",
            agent_id=agent_id,
            agent_name=agent_name,
            batch_job_id=batch_job_id,
            call_name=call_name,
            total_numbers=len(phone_numbers),
            scheduled_time=scheduled_time_str,
            recipients=[BatchCallRecipient(phone_number=phone) for phone in phone_numbers]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing batch calls: {str(e)}"
        )


@router.get("/batch-calling-status")
async def get_batch_calling_status(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get the status of all batch calling jobs for the current user from ElevenLabs API.
    This endpoint fetches live status for ALL batch jobs belonging to the current user.
    
    Returns:
        Live status for all batch calling jobs from ElevenLabs API
    """
    try:
        # Get all batch job IDs for the current user
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get all batch jobs for the current user (or all if super admin)
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT bc.batch_job_id, bc.call_name, bc.total_numbers, 
                           bc.scheduled_time_unix, bc.status, bc.created_at, bc.agent_id,
                           a.agent_name, u.name as user_name, u.email as user_email
                    FROM batch_calls bc
                    JOIN agents a ON bc.agent_id = a.agent_id
                    JOIN users u ON bc.user_id = u.id
                    ORDER BY bc.created_at DESC
                """)
            else:
                cursor.execute("""
                    SELECT bc.batch_job_id, bc.call_name, bc.total_numbers, 
                           bc.scheduled_time_unix, bc.status, bc.created_at, bc.agent_id,
                           a.agent_name, u.name as user_name, u.email as user_email
                    FROM batch_calls bc
                    JOIN agents a ON bc.agent_id = a.agent_id
                    JOIN users u ON bc.user_id = u.id
                    WHERE bc.user_id = %s
                    ORDER BY bc.created_at DESC
                """, (current_user.id,))
            
            batch_records = cursor.fetchall()
            
            if not batch_records:
                return {
                    "status": "success",
                    "message": "No batch calling jobs found for this user",
                    "user_email": current_user.email,
                    "total_jobs": 0,
                    "jobs": []
                }
        
        # Fetch live status from ElevenLabs for each batch job
        jobs_with_live_status = []
        successful_updates = 0
        failed_updates = 0
        
        for record in batch_records:
            batch_job_id = record[0]
            call_name = record[1]
            total_numbers = record[2]
            scheduled_time_unix = record[3]
            local_status = record[4]
            created_at = record[5]
            agent_id = record[6]
            agent_name = record[7]
            user_name = record[8]
            user_email = record[9]
            
            try:
                # Get live status from ElevenLabs
                status_response = requests.get(
                    f"https://api.elevenlabs.io/v1/convai/batch-calling/{batch_job_id}",
                    headers={
                        "xi-api-key": ELEVENLABS_API_KEY
                    },
                    timeout=30
                )
                
                if status_response.status_code == 200:
                    elevenlabs_status = status_response.json()
                    live_status = elevenlabs_status.get("status", "unknown")
                    
                    # Update local database if status changed
                    if live_status != local_status:
                        with get_db() as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE batch_calls 
                                SET status = %s, updated_at = NOW()
                                WHERE batch_job_id = %s
                            """, (live_status, batch_job_id))
                            conn.commit()
                    
                    job_data = {
                        "batch_job_id": batch_job_id,
                        "call_name": call_name,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "user_name": user_name,
                        "user_email": user_email,
                        "local_record": {
                            "total_numbers": total_numbers,
                            "scheduled_time_unix": scheduled_time_unix,
                            "previous_local_status": local_status,
                            "updated_status": live_status,
                            "created_at": created_at.isoformat() if created_at else None
                        },
                        "elevenlabs_live_status": elevenlabs_status,
                        "status_fetch": "success"
                    }
                    successful_updates += 1
                    
                else:
                    # If ElevenLabs API fails, use local data
                    job_data = {
                        "batch_job_id": batch_job_id,
                        "call_name": call_name,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "user_name": user_name,
                        "user_email": user_email,
                        "local_record": {
                            "total_numbers": total_numbers,
                            "scheduled_time_unix": scheduled_time_unix,
                            "local_status": local_status,
                            "created_at": created_at.isoformat() if created_at else None
                        },
                        "elevenlabs_live_status": None,
                        "status_fetch": "failed",
                        "error": f"ElevenLabs API error: {status_response.status_code}"
                    }
                    failed_updates += 1
                    
            except Exception as e:
                # Handle any request errors
                job_data = {
                    "batch_job_id": batch_job_id,
                    "call_name": call_name,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "user_name": user_name,
                    "user_email": user_email,
                    "local_record": {
                        "total_numbers": total_numbers,
                        "scheduled_time_unix": scheduled_time_unix,
                        "local_status": local_status,
                        "created_at": created_at.isoformat() if created_at else None
                    },
                    "elevenlabs_live_status": None,
                    "status_fetch": "error",
                    "error": str(e)
                }
                failed_updates += 1
            
            jobs_with_live_status.append(job_data)
        
        return {
            "status": "success",
            "message": f"Retrieved live status for {len(batch_records)} batch calling jobs",
            "user_email": current_user.email,
            "user_role": current_user.role,
            "total_jobs": len(batch_records),
            "successful_status_updates": successful_updates,
            "failed_status_updates": failed_updates,
            "jobs": jobs_with_live_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting batch calling status: {str(e)}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting batch calling status: {str(e)}"
        )


@router.get("/batch-calling-jobs")
async def list_batch_calling_jobs(
    current_user: User = Depends(get_current_active_user)
):
    """
    List all batch calling jobs for the current user.
    Super admin can see all jobs.
    
    Returns:
        List of batch calling jobs
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or regular user
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT bc.batch_job_id, bc.call_name, bc.total_numbers, 
                           bc.scheduled_time_unix, bc.status, bc.created_at,
                           a.agent_name, u.name as user_name
                    FROM batch_calls bc
                    JOIN agents a ON bc.agent_id = a.agent_id
                    JOIN users u ON bc.user_id = u.id
                    ORDER BY bc.created_at DESC
                """)
            else:
                cursor.execute("""
                    SELECT bc.batch_job_id, bc.call_name, bc.total_numbers, 
                           bc.scheduled_time_unix, bc.status, bc.created_at,
                           a.agent_name, u.name as user_name
                    FROM batch_calls bc
                    JOIN agents a ON bc.agent_id = a.agent_id
                    JOIN users u ON bc.user_id = u.id
                    WHERE bc.user_id = %s
                    ORDER BY bc.created_at DESC
                """, (current_user.id,))
            
            batch_jobs = cursor.fetchall()
            
            jobs_list = []
            for job in batch_jobs:
                jobs_list.append({
                    "batch_job_id": job[0],
                    "call_name": job[1],
                    "total_numbers": job[2],
                    "scheduled_time_unix": job[3],
                    "status": job[4],
                    "created_at": job[5].isoformat() if job[5] else None,
                    "agent_name": job[6],
                    "user_name": job[7]
                })
            
            return {
                "status": "success",
                "total_jobs": len(jobs_list),
                "jobs": jobs_list
            }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error listing batch calling jobs: {str(e)}"
        )


@router.post("/cancel-batch-calling")
async def cancel_batch_calling(
    call_name: str = Form(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Cancel a batch calling job using the call_name.
    Looks up the batch_job_id from database and cancels the job via ElevenLabs API.
    
    Args:
        call_name: The name of the batch calling job to cancel
        
    Returns:
        Cancellation status and details
    """
    try:
        # Get batch job details from database using call_name
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the batch job
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT batch_job_id, agent_id, total_numbers, status, created_at
                    FROM batch_calls 
                    WHERE call_name = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (call_name,))
            else:
                cursor.execute("""
                    SELECT batch_job_id, agent_id, total_numbers, status, created_at
                    FROM batch_calls 
                    WHERE call_name = %s AND user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (call_name, current_user.id))
            
            batch_record = cursor.fetchone()
            if not batch_record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Batch calling job with name '{call_name}' not found or you don't have permission to cancel it"
                )
            
            batch_job_id, agent_id, total_numbers, current_status, created_at = batch_record
        
        # Check if job can be cancelled
        if current_status in ["completed", "cancelled", "failed"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel batch job. Current status: {current_status}"
            )
        
        # Cancel batch calling job via ElevenLabs API
        cancel_response = requests.post(
            f"https://api.elevenlabs.io/v1/convai/batch-calling/{batch_job_id}/cancel",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            },
            timeout=30
        )
        
        if cancel_response.status_code not in [200, 204]:
            raise HTTPException(
                status_code=cancel_response.status_code,
                detail=f"Failed to cancel batch calling job: {cancel_response.text}"
            )
        
        # Update status in database
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE batch_calls 
                SET status = 'cancelled', updated_at = NOW()
                WHERE batch_job_id = %s
            """, (batch_job_id,))
            conn.commit()
        
        cancel_result = cancel_response.json() if cancel_response.text else {}
        
        return {
            "status": "success",
            "message": f"Batch calling job '{call_name}' cancelled successfully",
            "call_name": call_name,
            "batch_job_id": batch_job_id,
            "agent_id": agent_id,
            "total_numbers": total_numbers,
            "previous_status": current_status,
            "current_status": "cancelled",
            "cancelled_by": current_user.name,
            "elevenlabs_response": cancel_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error cancelling batch calling job: {str(e)}"
        )


@router.post("/retry-batch-calling")
async def retry_batch_calling(
    call_name: str = Form(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retry a batch calling job using the call_name.
    Looks up the batch_job_id from database and retries the job via ElevenLabs API.
    Checks live status from ElevenLabs before attempting retry.
    
    Args:
        call_name: The name of the batch calling job to retry
        
    Returns:
        Retry status and details
    """
    try:
        # Get batch job details from database using call_name
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the batch job
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT bc.batch_job_id, bc.agent_id, bc.total_numbers, bc.status, a.agent_name
                    FROM batch_calls bc
                    JOIN agents a ON bc.agent_id = a.agent_id
                    WHERE bc.call_name = %s
                    ORDER BY bc.created_at DESC
                    LIMIT 1
                """, (call_name,))
            else:
                cursor.execute("""
                    SELECT bc.batch_job_id, bc.agent_id, bc.total_numbers, bc.status, a.agent_name
                    FROM batch_calls bc
                    JOIN agents a ON bc.agent_id = a.agent_id
                    WHERE bc.call_name = %s AND bc.user_id = %s
                    ORDER BY bc.created_at DESC
                    LIMIT 1
                """, (call_name, current_user.id))
            
            batch_record = cursor.fetchone()
            
            if not batch_record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Batch calling job not found or you don't have permission to retry it"
                )
            
            batch_job_id, agent_id, total_numbers, local_status, agent_name = batch_record
        
        print(f"Checking live status for batch job: {batch_job_id}")
        
        # Get live status from ElevenLabs API first
        try:
            status_response = requests.get(
                f"https://api.elevenlabs.io/v1/convai/batch-calling/{batch_job_id}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY
                },
                timeout=30
            )
            
            if status_response.status_code != 200:
                raise HTTPException(
                    status_code=status_response.status_code,
                    detail=f"Failed to get current status from ElevenLabs: {status_response.text}"
                )
            
            elevenlabs_status_data = status_response.json()
            live_status = elevenlabs_status_data.get("status", "unknown")
            
            print(f"Live status from ElevenLabs: {live_status}")
            
            # Update local database with live status
            if live_status != local_status:
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE batch_calls 
                        SET status = %s, updated_at = NOW()
                        WHERE batch_job_id = %s
                    """, (live_status, batch_job_id))
                    conn.commit()
                    print(f"Updated local status from '{local_status}' to '{live_status}'")
            
        except requests.exceptions.RequestException as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch live status from ElevenLabs: {str(e)}"
            )
        
        # Check if job can be retried based on LIVE status from ElevenLabs
        if live_status in ["in_progress", "pending", "submitted", "retrying"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot retry job with current ElevenLabs status '{live_status}'. Job must be completed, failed, or cancelled to retry."
            )
        
        print(f"Retrying batch calling job: {batch_job_id} (current status: {live_status})")
        
        # Retry batch calling job via ElevenLabs API
        retry_response = requests.post(
            f"https://api.elevenlabs.io/v1/convai/batch-calling/{batch_job_id}/retry",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            },
            timeout=30
        )
        
        if retry_response.status_code not in [200, 201]:
            raise HTTPException(
                status_code=retry_response.status_code,
                detail=f"Failed to retry batch calling job: {retry_response.text}"
            )
        
        # Update status in database to reflect retry
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE batch_calls 
                SET status = %s, updated_at = NOW()
                WHERE batch_job_id = %s
            """, ("retrying", batch_job_id))
            conn.commit()
        
        retry_result = retry_response.json() if retry_response.text else {}
        
        return {
            "status": "success",
            "message": f"Batch calling job '{call_name}' retry initiated successfully",
            "call_name": call_name,
            "batch_job_id": batch_job_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "total_numbers": total_numbers,
            "previous_status": live_status,  # Use live status instead of local
            "current_status": "retrying",
            "retried_by": current_user.name,
            "elevenlabs_live_status": elevenlabs_status_data,
            "elevenlabs_response": retry_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrying batch calling job: {str(e)}"
        )


@router.get("/batch-calling-status-by-name/{call_name}")
async def get_batch_calling_status_by_name(
    call_name: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get the status of a batch calling job using the call_name.
    Looks up the batch_job_id from database and gets status from ElevenLabs API.
    
    Args:
        call_name: The name of the batch calling job
        
    Returns:
        Batch calling job status and details
    """
    try:
        # Get batch job details from database using call_name
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the batch job
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT batch_job_id, agent_id, total_numbers, scheduled_time_unix, 
                           status, created_at, updated_at
                    FROM batch_calls 
                    WHERE call_name = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (call_name,))
            else:
                cursor.execute("""
                    SELECT batch_job_id, agent_id, total_numbers, scheduled_time_unix, 
                           status, created_at, updated_at
                    FROM batch_calls 
                    WHERE call_name = %s AND user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (call_name, current_user.id))
            
            batch_record = cursor.fetchone()
            if not batch_record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Batch calling job with name '{call_name}' not found or you don't have permission to view it"
                )
            
            batch_job_id, agent_id, total_numbers, scheduled_time_unix, local_status, created_at, updated_at = batch_record
        
        # Get batch calling status from ElevenLabs
        status_response = requests.get(
            f"https://api.elevenlabs.io/v1/convai/batch-calling/{batch_job_id}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            },
            timeout=30
        )
        
        if status_response.status_code != 200:
            raise HTTPException(
                status_code=status_response.status_code,
                detail=f"Failed to get batch calling status from ElevenLabs: {status_response.text}"
            )
        
        batch_status = status_response.json()
        
        # Update local status if it's different from ElevenLabs
        elevenlabs_status = batch_status.get("status", "unknown")
        if elevenlabs_status != local_status:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE batch_calls 
                    SET status = %s, updated_at = NOW()
                    WHERE batch_job_id = %s
                """, (elevenlabs_status, batch_job_id))
                conn.commit()
                local_status = elevenlabs_status
        
        return {
            "status": "success",
            "call_name": call_name,
            "batch_job_id": batch_job_id,
            "agent_id": agent_id,
            "local_record": {
                "total_numbers": total_numbers,
                "scheduled_time_unix": scheduled_time_unix,
                "local_status": local_status,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None
            },
            "elevenlabs_status": batch_status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting batch calling status: {str(e)}"
        )
        

