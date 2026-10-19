import os, secrets
from app.main import Base, engine, DB, User, ROLES, password_hash
from sqlalchemy import select

def seed():
    Base.metadata.create_all(engine)
    with DB() as db:
        for role in ROLES:
            email=role+'@demo.local'
            if db.scalar(select(User).where(User.email==email)): continue
            password=secrets.token_urlsafe(18)
            db.add(User(email=email,password=password_hash(password),role=role,department='Operations'))
            print(email+'  '+password)
        db.commit()
if __name__=='__main__': seed()
