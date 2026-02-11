"""Test suite for Rounds diagnostic system.

Organized into three categories:

1. core/: Unit tests for core domain logic
   - Minimal dependencies, fast execution
   - Uses in-memory fakes for ports

2. adapters/: Integration tests for adapter implementations
   - Tests against real or mocked external systems
   - Validates adapter behavior and error handling

3. fakes/: Port implementations for testing
   - In-memory implementations of TelemetryPort, StorePort, etc.
   - Used by core unit tests
"""
