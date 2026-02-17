from pydantic import BaseModel # Basically makes sure users can only send correct data type. So someone cant set their full name as 69420. (needed for fastapi)

# Just for input validation
class UserCreate(BaseModel):
    username: str
    age_group: str
    location: str