from fastapi import APIRouter, Depends, HTTPException, status
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import os
import shutil
import base64
import requests
import csv
import io
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
    agent_id: str = Form(...),
    csv_file: UploadFile = File(...),
    phone_column: str = Form("phone"),  # Default column name for phone numbers
    call_name: str = Form(...),  # Name for the batch calling job
    scheduled_time_unix: Optional[int] = Form(None),  # Optional scheduled time (Unix timestamp)
    current_user: User = Depends(get_current_active_user)
):
    """
    Perform batch calling using ElevenLabs batch calling API with a CSV file containing phone numbers.
    
    Args:
        agent_id: The ID of the agent to use for calling
        csv_file: CSV file containing phone numbers
        phone_column: Name of the column containing phone numbers (default: 'phone')
        call_name: Name for the batch calling job
        scheduled_time_unix: Optional Unix timestamp for scheduling calls (if not provided, calls start immediately)
        
    Returns:
        BatchCallResponse with batch job details
    """
    try:
        # Validate CSV file type
        if not csv_file.filename.endswith('.csv'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only CSV files are allowed"
            )
        
        # Validate CSV content type
        if csv_file.content_type not in ['text/csv', 'application/csv', 'application/vnd.ms-excel']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid content type. Expected CSV file. Received: {csv_file.content_type}"
            )
        
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
                    detail="Agent not found or you don't have permission to use it"
                )
            
            agent_name, phone_number_id, twilio_number, user_id = agent_data

        if not phone_number_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent doesn't have a phone number configured"
            )

        # Read and parse CSV file
        csv_content = await csv_file.read()
        csv_string = csv_content.decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(csv_string))
        
        # Extract phone numbers from CSV
        phone_numbers = []
        row_count = 0
        
        for row in csv_reader:
            row_count += 1
            if phone_column not in row:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Column '{phone_column}' not found in CSV. Available columns: {list(row.keys())}"
                )
            
            phone_number = row[phone_column].strip()
            if phone_number:  # Only add non-empty phone numbers
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
                detail="No valid phone numbers found in CSV file"
            )
        
        print(f"Found {len(phone_numbers)} valid phone numbers for batch calling")
        
        # Prepare recipients for ElevenLabs batch calling API
        recipients = [{"phone_number": phone} for phone in phone_numbers]
        
        # Prepare the batch calling payload
        batch_payload = {
            "call_name": call_name,
            "agent_id": agent_id,
            "agent_phone_number_id": phone_number_id,
            "recipients": recipients
        }
        
        
        # Add scheduled time if provided
        if scheduled_time_unix:
            batch_payload["scheduled_time_unix"] = scheduled_time_unix
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
                scheduled_time_unix, "submitted"
            ))
            conn.commit()
            print(f"Batch calling record saved to database")
        
        # Format response
        scheduled_time_str = None
        if scheduled_time_unix:
            from datetime import datetime
            scheduled_time_str = datetime.fromtimestamp(scheduled_time_unix).isoformat()
        
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


@router.get("/batch-calling-status/{call_name}")
async def get_batch_calling_status(
    call_name: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Get the status of a batch calling job from ElevenLabs using call_name.
    
    Args:
        call_name: The name of the batch calling job
        
    Returns:
        Batch calling job status and details
    """
    try:
        # Get batch_job_id from database using call_name
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the batch job
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT id, batch_job_id, call_name, total_numbers, scheduled_time_unix, status, created_at
                    FROM batch_calls 
                    WHERE call_name = %s
                """, (call_name,))
            else:
                cursor.execute("""
                    SELECT id, batch_job_id, call_name, total_numbers, scheduled_time_unix, status, created_at
                    FROM batch_calls 
                    WHERE call_name = %s AND user_id = %s
                """, (call_name, current_user.id))
            
            batch_record = cursor.fetchone()
            if not batch_record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Batch calling job not found or you don't have permission to view it"
                )
            
            batch_job_id = batch_record[1]
        
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
                detail=f"Failed to get batch calling status: {status_response.text}"
            )
        
        batch_status = status_response.json()
        
        return {
            "status": "success",
            "call_name": call_name,
            "batch_job_id": batch_job_id,
            "local_record": {
                "call_name": batch_record[2],
                "total_numbers": batch_record[3],
                "scheduled_time_unix": batch_record[4],
                "local_status": batch_record[5],
                "created_at": batch_record[6].isoformat() if batch_record[6] else None
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


@router.post("/batch-calling-cancel/{call_name}")
async def cancel_batch_calling(
    call_name: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Cancel a batch calling job using call_name.
    
    Args:
        call_name: The name of the batch calling job to cancel
        
    Returns:
        Cancellation status and details
    """
    try:
        # Get batch_job_id from database using call_name
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if user is super admin or owns the batch job
            if current_user.role.lower() == "super admin":
                cursor.execute("""
                    SELECT id, batch_job_id, call_name, total_numbers, status, created_at, user_id
                    FROM batch_calls 
                    WHERE call_name = %s
                """, (call_name,))
            else:
                cursor.execute("""
                    SELECT id, batch_job_id, call_name, total_numbers, status, created_at, user_id
                    FROM batch_calls 
                    WHERE call_name = %s AND user_id = %s
                """, (call_name, current_user.id))
            
            batch_record = cursor.fetchone()
            if not batch_record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Batch calling job not found or you don't have permission to cancel it"
                )
            
            batch_job_id = batch_record[1]
            current_status = batch_record[4]
        
        # Check if the job can be cancelled
        if current_status in ["cancelled", "completed", "failed"]:
            return {
                "status": "info",
                "message": f"Batch calling job '{call_name}' is already {current_status} and cannot be cancelled",
                "call_name": call_name,
                "batch_job_id": batch_job_id,
                "current_status": current_status
            }
        
        # Cancel batch calling job via ElevenLabs API
        cancel_response = requests.post(
            f"https://api.elevenlabs.io/v1/convai/batch-calling/{batch_job_id}/cancel",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            },
            timeout=30
        )
        
        if cancel_response.status_code != 200:
            raise HTTPException(
                status_code=cancel_response.status_code,
                detail=f"Failed to cancel batch calling job: {cancel_response.text}"
            )
        
        cancel_result = cancel_response.json()
        
        # Update local database status
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE batch_calls 
                SET status = %s, updated_at = NOW()
                WHERE batch_job_id = %s
            """, ("cancelled", batch_job_id))
            conn.commit()
            
            print(f"✅ Batch calling job {call_name} cancelled and database updated")
        
        return {
            "status": "success",
            "message": f"Batch calling job '{call_name}' has been cancelled successfully",
            "call_name": call_name,
            "batch_job_id": batch_job_id,
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
        

