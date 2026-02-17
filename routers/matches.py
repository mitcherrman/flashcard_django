from fastapi import Depends, APIRouter
from sqlalchemy.orm import Session # Sessions is used so i can query and add/delete/update shit in my dataset.  allows me to interact with tables, allows me to add user on table everytime someone register account

from database import get_db
from models.user import User

from services.elo import update_elo
from services.redis import redis_client, LEADERBOARD_KEY

router = APIRouter(prefix = "/matches", tags = ["matches"])

@router.post("/")
def submit_result(
    user_a_id: int,
    user_b_id: int,
    winner_id: int,
    db: Session = Depends(get_db)
):
    
    # Returns user with matching id as a User object in the db (can access other variables of the row)
    user_a = db.get(User, user_a_id)
    user_b = db.get(User, user_b_id)


    # Returns error if user doesn't expist in db
    if not user_a or not user_b:
        return {"error": "Invalid users"}

    # Changes elo after the match
    update_elo(user_a, user_b, winner_id)

    # Saves changes in db
    db.commit()

    # Sync Redis
    redis_client.zadd(
        LEADERBOARD_KEY,
        {
            str(user_a.id): user_a.elo,
            str(user_b.id): user_b.elo
        }
    )

    # Returns the elo to frontend (idk if you would want to display the new elo or something but feel free to delete idk)
    return {
        "user_a_elo": user_a.elo,
        "user_b_elo": user_b.elo
    }
