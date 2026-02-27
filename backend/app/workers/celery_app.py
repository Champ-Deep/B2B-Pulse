from celery import Celery

from app.config import settings

celery_app = Celery(
    "autoengage",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.polling_tasks",
        "app.workers.engagement_tasks",
        "app.workers.session_monitor",
        "app.workers.stale_actions",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "poll-tracked-pages": {
            "task": "app.workers.polling_tasks.dispatch_poll_tasks",
            "schedule": 300.0,  # Every 5 minutes
        },
        "check-linkedin-sessions": {
            "task": "app.workers.session_monitor.check_linkedin_sessions",
            "schedule": 21600.0,  # Every 6 hours
        },
        "cleanup-stale-actions": {
            "task": "app.workers.stale_actions.cleanup_stale_actions",
            "schedule": 600.0,  # Every 10 minutes
        },
    },
)
