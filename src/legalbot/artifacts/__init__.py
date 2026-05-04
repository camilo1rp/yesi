"""Artifact layer — versioned, addressable step outputs (§7.7 of the plan)."""

from legalbot.artifacts.models import ArtifactRef
from legalbot.artifacts.service import ArtifactService

__all__ = ["ArtifactRef", "ArtifactService"]
