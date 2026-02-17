from sqlalchemy import Column, Integer, String # sqlalchemy used to so i can use py to talk to sql, these data types imported to set them to each var in the tables in the database
from sqlalchemy.orm import declarative_base

Base = declarative_base() # parent class for all database tables, used to track what are classes are tables

# Class user defines the table of the users itself. What attributes each users can have and shi
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key = True) # The primary key of each column in a table. Always unique and used to id a user (in frontend and elswhere idk)(Prolly gonna set up the system to auto assign to users)
    # The rest of these are just vars in a usr profile
    username = Column(String, nullable = False)
    age_group = Column(String, nullable = False)
    location = Column(String, nullable = False)

    elo = Column(Integer, default = 1000) # Sets default elo of user to 1000
    comparisons = Column(Integer, default = 0) # Num of comparisons of a user 