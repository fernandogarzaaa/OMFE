import os

# Cheap argon2 cost for the test suite (production refuses these values).
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_COST", "8")
os.environ.setdefault("ARGON2_PARALLELISM", "1")
os.environ.setdefault("ENV", "test")
