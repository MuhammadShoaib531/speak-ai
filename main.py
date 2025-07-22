from fastapi import FastAPI

from database import create_tables
from routers import user_signup
from routers.agent import router as agent_router

app = FastAPI(title="SpeakAI API", description="API for SpeakAI application", version="1.0.0")

# Include routers
app.include_router(user_signup.router)
app.include_router(agent_router)

# Create database tables on startup
@app.on_event("startup")
async def startup_event():
    create_tables()

@app.get("/")
async def root():
    return {"message": "Welcome to SpeakAI API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000) 