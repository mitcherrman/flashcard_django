from fastapi import FastAPI

from database import engine
from models.user import Base
from routers import users, leaderboard, match, matches

app = FastAPI()

# Creates table based on the model of table created above. delete after first compilation cause only wanna create the table once
Base.metadata.create_all(bind = engine) 

# Register routes
app.include_router(users.router)
app.include_router(leaderboard.router)
app.include_router(match.router)
app.include_router(matches.router)
