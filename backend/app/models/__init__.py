"""SQLAlchemy ORM models.

Importing this package registers every model on ``Base.metadata`` so a single
``import app.models`` is enough for table creation and Alembic autogenerate.
"""

from app.models.generated_sentence import GeneratedSentence
from app.models.review_record import ReviewRecord
from app.models.user import User
from app.models.word_entry import WordEntry

__all__ = [
    "User",
    "WordEntry",
    "ReviewRecord",
    "GeneratedSentence",
]
