"""Composition root for the Rounds diagnostic system.

This module is the ONLY location that imports both core domain logic
and concrete adapter implementations. All wiring of dependencies
happens here, creating a clear entry point for the application.

Module Structure:
- Configuration loading via config module
- Adapter instantiation
- Core service initialization
- Dependency injection
- Entry point selection (daemon, CLI, webhook, etc.)
"""

from typing import NoReturn


def main() -> NoReturn:
    """Application entry point.

    This function would load configuration, instantiate adapters,
    initialize core services, and start the appropriate run mode
    (daemon polling loop, CLI commands, webhook server, etc.).

    To be implemented in Phase 4 when core ports and adapters are ready.
    """
    raise NotImplementedError("main.py composition root to be implemented in Phase 4")


if __name__ == "__main__":
    main()
