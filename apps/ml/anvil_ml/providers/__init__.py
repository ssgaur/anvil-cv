"""Provider package: SAM2 and SAM3 behind one interface."""

from anvil_ml.providers.base import (
    Capabilities,
    CapabilityError,
    Instance,
    ModelProvider,
)
from anvil_ml.providers.registry import (
    available,
    capabilities_of,
    get_provider,
    list_capabilities,
    release,
    release_all,
)

__all__ = [
    "Capabilities",
    "CapabilityError",
    "Instance",
    "ModelProvider",
    "available",
    "capabilities_of",
    "get_provider",
    "list_capabilities",
    "release",
    "release_all",
]
