from fastapi import APIRouter, Depends, HTTPException, status
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import os
import shutil
import base64
import requests
from pydantic import EmailStr
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
        os.makedirs("uploads", exist_ok=True)
        file_path = os.path.join("uploads", file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_name = file.filename
        s3_key = f"user_docs/{email}/{file.filename}"
        file_url = upload_to_s3(file_path, s3_key)

        encoded_file = base64.b64encode(open(file_path, "rb").read()).decode("utf-8")
        files = {'file': (file.filename, base64.b64decode(encoded_file), 'application/pdf')}

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
        
        # Then insert the agent data
        cursor.execute("""
            INSERT INTO agents (
                user_id, agent_id, agent_name, first_message, prompt, llm,
                documentation_id, file_name, file_url, voice_id, twilio_number,
                business_name, agent_type, speaking_style
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) RETURNING id
        """, (
            user_id, agent_id, agent_name, first_message, prompt, llm,
            documentation_id, file_name, file_url, voice_id, twilio_number,
            business_name, agent_type, speaking_style
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
                   file_name, file_url, voice_id, business_name, agent_type, speaking_style
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
        current_business_name = business_name if business_name else existing_agent[9]
        current_agent_type = agent_type if agent_type else existing_agent[10]
        current_speaking_style = speaking_style if speaking_style else existing_agent[11]

    # Handle file upload if provided
    if file is not None:
        os.makedirs("uploads", exist_ok=True)
        file_path = os.path.join("uploads", file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        current_file_name = file.filename
        s3_key = f"user_docs/{email}/{file.filename}"
        current_file_url = upload_to_s3(file_path, s3_key)

        encoded_file = base64.b64encode(open(file_path, "rb").read()).decode("utf-8")
        files = {'file': (file.filename, base64.b64decode(encoded_file), 'application/pdf')}

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
                business_name = %s, agent_type = %s, speaking_style = %s
            WHERE agent_id = %s AND user_id = %s
        """, (
            current_agent_name, current_first_message, current_prompt, current_llm,
            current_documentation_id, current_file_name, current_file_url, current_voice_id,
            current_business_name, current_agent_type, current_speaking_style,
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
                    SELECT id, user_id, agent_name, twilio_number, voice_id
                    FROM agents 
                    WHERE agent_id = %s
                """, (agent_id,))
            else:
                # Regular user can only delete their own agents
                cursor.execute("""
                    SELECT id, user_id, agent_name, twilio_number, voice_id
                    FROM agents 
                    WHERE agent_id = %s AND user_id = %s
                """, (agent_id, current_user.id))
            
            agent_data = cursor.fetchone()
            if not agent_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found or you don't have permission to delete it"
                )
            
            db_id, user_id, agent_name, twilio_number, voice_id = agent_data

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

        # Step 3: Get phone number ID and delete from ElevenLabs phone numbers
        try:
            # First, get all phone numbers from ElevenLabs to find the right one
            phone_numbers_response = requests.get(
                "https://api.elevenlabs.io/v1/convai/phone-numbers",
                headers={"xi-api-key": ELEVENLABS_API_KEY},
                timeout=30
            )
            
            if phone_numbers_response.status_code == 200:
                phone_numbers_data = phone_numbers_response.json()
                phone_number_id = None
                
                # Find the phone number ID that matches our agent's Twilio number
                for phone_entry in phone_numbers_data.get("phone_numbers", []):
                    if phone_entry.get("phone_number") == twilio_number:
                        phone_number_id = phone_entry.get("phone_number_id")
                        break
                
                # Delete phone number from ElevenLabs if found
                if phone_number_id:
                    delete_phone_response = requests.delete(
                        f"https://api.elevenlabs.io/v1/convai/phone-numbers/{phone_number_id}",
                        headers={"xi-api-key": ELEVENLABS_API_KEY},
                        timeout=30
                    )
                    
                    if delete_phone_response.status_code not in [200, 204, 404]:
                        print(f"Warning: Failed to delete phone number from ElevenLabs. Status: {delete_phone_response.status_code}")
                        
        except Exception as e:
            print(f"Warning: Error deleting phone number from ElevenLabs: {str(e)}")

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