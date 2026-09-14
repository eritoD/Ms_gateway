"""Repository layer for persistent data sources.

The gateway is currently stateless, so it deliberately has no concrete
repository. This package exists to keep the shared Service–Repository layout;
repositories must be added only when the service owns persistent data.
"""
