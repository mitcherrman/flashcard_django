from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from database import engine
from models.user import Base
from routers import users, leaderboard, match, matches

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins, change to specific IPs in production
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (POST, GET, etc.)
    allow_headers=["*"],  # Allows all headers
)

# Creates table based on the model of table created above. delete after first compilation cause only wanna create the table once
Base.metadata.create_all(bind = engine) 

# Register routes
app.include_router(users.router)
app.include_router(leaderboard.router)
app.include_router(match.router)
app.include_router(matches.router)
