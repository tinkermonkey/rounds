"""Sample target library used as a fixture for end-to-end diagnosis tests.

This is a deliberately small, buggy codebase standing in for a real
target project. It is not part of the rounds package itself — it exists
so integration tests can trigger a known error and verify that a
diagnosis engine can trace it back to the exact source line.
"""
