-- Enable pgvector in the application database. Runs once on first container start.
CREATE EXTENSION IF NOT EXISTS vector;
