from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("postgresql://localhost/payments")
session = sessionmaker(bind=engine)()
