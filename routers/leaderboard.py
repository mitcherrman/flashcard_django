from fastapi import APIRouter # Allows to group routes
from services.redis import redis_client, LEADERBOARD_KEY

router = APIRouter(prefix = "/leaderboard", tags = ["leaderboard"]) # All routes with /leaderboard would get routed

@router.get("/")
def get_leaderboard(limit: int = 50): # Limit number of returns (default is top 50 rn)
    return redis_client.zrevrange(
        LEADERBOARD_KEY, # The set to perform on
        0, # Starting index
        limit - 1, # Ending index
        withscores = True # Enables implementation of elo 
    )
