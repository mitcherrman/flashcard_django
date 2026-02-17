from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import random

from models.user import User
from database import get_db

router = APIRouter(prefix="/match", tags=["match"])

@router.get("/")
def get_match(db: Session = Depends(get_db)):
    users = db.query(User).all() # Fetches all the users from the db (will find another way later)

    # Will returns error if there is 0-1 users in the db (Only for possible sqlalchemy errors)
    if len(users) < 2:
        return {"error": "Not enough users"}

    user_a, user_b = random.sample(users, 2) # Randomly chooses two users from the db

    # Returns the chosen users
    return {
        "user_a": {
            "id": user_a.id,
            "username": user_a.username,
            "elo": user_a.elo
        },
        "user_b": {
            "id": user_b.id,
            "username": user_b.username,
            "elo": user_b.elo
        }
    }
