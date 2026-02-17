from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("sqlite:///app.db") # allows sqlalchemy to connect to the database itself (link is url of database)
SessionLocal = sessionmaker(bind = engine) # allows you to create sessions. call SessionLocal() to make a new session each time

# Create a session for requests to the database 
def get_db():
    db = SessionLocal() # Used to create and declare a session
    
    try:
        yield db # gives db(the session) to endpoint (wait until all the changes in the requests are made) 
    finally:
        db.close() # closes the session when request/changes are done