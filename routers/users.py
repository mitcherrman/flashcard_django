# User endpoints
from fastapi import Depends, APIRouter
from sqlalchemy.orm import Session # Sessions is used so i can query and add/delete/update shit in my dataset.  allows me to interact with tables, allows me to add user on table everytime someone register account

from database import get_db
from models.user import User
from schemas.user import UserCreate

from services.redis import redis_client, LEADERBOARD_KEY

router = APIRouter(prefix = "/users", tags = ["users"])

@router.post("/") # Means will run func below when someone sends post req to /users path
def create_user(user: UserCreate, db: Session = Depends(get_db)): # used to call get_db to make changes and stuff to the database and pass whatevers in get_db into this func
    new_user = User(username = user.username, age_group = user.age_group, location = user.location) # to represent a row of the users table
    
    db.add(new_user) # used to insert things into database db
    db.commit() # saves whatever you just did to db and assigns id to the new user (uses next available num) (starts with 1)
    db.refresh(new_user) # runs "SELECT * FROM users WHERE id = <newly_created_id>;" to update the newly assigned id to python as well
    
    # Syncs redis leaderboard with the sql database
    redis_client.zadd(
        LEADERBOARD_KEY,
        {str(new_user.id): new_user.elo}
    )    
    
    return new_user # after func returns new_user, FastAPI converts into JSON and sends back as HTTP response that can be given to frontend