"""Shared SQLAlchemy instance — imported by both models and app."""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
