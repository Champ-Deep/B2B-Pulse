import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.post import Post
from app.models.tracked_page import TrackedPage
from app.services.url_utils import (
    extract_facebook_post_id,
    extract_instagram_post_id,
    extract_linkedin_post_id,
    is_facebook_url,
    is_instagram_url,
    is_linkedin_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WhatsAppLinkEvent(BaseModel):
    url: str
    group_name: str
    sender: str
    timestamp: str


@router.post("/whatsapp-link")
async def handle_whatsapp_link(
    event: WhatsAppLinkEvent,
    db: AsyncSession = Depends(get_db),
):
    parsed = urlparse(event.url)
    domain = parsed.netloc.lower()

    # Only process social media links
    supported_domains = [
        "linkedin.com",
        "www.linkedin.com",
        "instagram.com",
        "www.instagram.com",
        "instagr.am",
        "facebook.com",
        "www.facebook.com",
        "fb.com",
        "m.facebook.com",
        "web.facebook.com",
    ]
    if not any(d in domain for d in supported_domains):
        return {"status": "ignored", "reason": "Not a supported social media URL"}

    # Check if this matches a tracked page
    result = await db.execute(select(TrackedPage).where(TrackedPage.active.is_(True)))
    tracked_pages = result.scalars().all()

    # Determine platform from URL for matching
    is_meta_url = is_instagram_url(event.url) or is_facebook_url(event.url)

    matched_page = None
    for page in tracked_pages:
        if (
            page.url
            and urlparse(page.url).netloc == parsed.netloc
            and page.external_id
            and page.external_id in parsed.path
        ):
            matched_page = page
            break
        # For Meta URLs, also match by platform (IG/FB pages may have different domains)
        if (
            is_meta_url
            and page.platform.value == "meta"
            and page.external_id
            and page.external_id in parsed.path
        ):
            matched_page = page
            break

    if matched_page:
        # Extract post ID from URL based on platform
        external_post_id = None
        if is_linkedin_url(event.url):
            external_post_id = extract_linkedin_post_id(event.url)
        elif is_instagram_url(event.url):
            external_post_id = extract_instagram_post_id(event.url)
            if external_post_id:
                external_post_id = f"ig_{external_post_id}"
        elif is_facebook_url(event.url):
            external_post_id = extract_facebook_post_id(event.url)

        if not external_post_id:
            # Use full path as fallback identifier
            external_post_id = parsed.path.strip("/")

        if not external_post_id:
            return {"status": "error", "message": "Could not extract post identifier from URL"}

        # Deduplicate: check if we already processed this post
        existing = await db.execute(select(Post).where(Post.external_post_id == external_post_id))
        if existing.scalar_one_or_none():
            return {"status": "duplicate", "message": "Post already processed"}

        # Create Post record (catch IntegrityError for concurrent duplicates)
        post = Post(
            tracked_page_id=matched_page.id,
            platform=matched_page.platform,
            external_post_id=external_post_id,
            url=event.url,
        )
        db.add(post)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return {"status": "duplicate", "message": "Post already processed (concurrent)"}

        logger.info(f"New post from WhatsApp: {event.url} -> post {post.id}")

        # Enqueue engagement jobs via Celery
        from app.workers.engagement_tasks import schedule_staggered_engagements

        schedule_staggered_engagements.delay(str(post.id), str(matched_page.id))

        return {
            "status": "matched",
            "post_id": str(post.id),
            "tracked_page_id": str(matched_page.id),
            "message": "Engagement jobs scheduled",
        }

    return {
        "status": "unmatched",
        "url": event.url,
        "message": "URL does not match any tracked page. Consider adding it.",
    }
