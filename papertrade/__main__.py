from warehouse import db
from .book import update
update(db.connect(read_only=True))
