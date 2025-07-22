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
from models import Agent

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
                "prompt": prompt_block
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