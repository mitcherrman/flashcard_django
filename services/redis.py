import redis

# Connects host to redis
redis_client = redis.Redis(
    host = "localhost",
    port = 6379, # Default port for redis
    decode_responses = True
)

LEADERBOARD_KEY = "leaderboard" # Naming sorted set for redis