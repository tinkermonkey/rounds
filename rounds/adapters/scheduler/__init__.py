"""Scheduler adapters for driving the poll loop.

Implementations support multiple scheduling strategies:
- Daemon (asyncio event loop with configurable interval)
- Cron (integration with system cron)
- Kubernetes (CronJob resource)
"""
