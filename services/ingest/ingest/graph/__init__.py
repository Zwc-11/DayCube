"""The knowledge graph: entities the records name, and how they met."""

from .extract import Graph, canonical_url, courses, deadlines, extract

__all__ = ["Graph", "canonical_url", "courses", "deadlines", "extract"]
