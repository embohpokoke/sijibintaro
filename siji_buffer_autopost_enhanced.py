#!/usr/bin/env python3
"""
SIJI Buffer Auto-Post Enhanced
Weekly automated caption + image generation → Buffer Ideas (full metadata)
Fully leverages Buffer's GraphQL API capabilities
"""

import os, sys, requests, json, logging, random
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv('.env.buffer')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('logs/buffer_autopost.log'), logging.StreamHandler()]
)
logger = logging.getLogger()

BUFFER_API_TOKEN = os.getenv("BUFFER_API_TOKEN", "").strip()
BUFFER_API_URL = "https://api.buffer.com"
BUFFER_ORG_ID = "69b39c8a4eae0eec66762adc"
BUFFER_IG_ID = os.getenv("BUFFER_IG_ID", "").strip()
BUFFER_TIKTOK_ID = os.getenv("BUFFER_TIKTOK_ID", "").strip()
MEDIA_PATH = "/opt/sijibintaro/media/"

CAPTION_TEMPLATES = {
    "promo": {
        "text": "🚀 Promo Minggu! Hemat cuci 08:00-16:00, bayar sekarang 25% ✨ Lokasi: Jl. Raya Emerald Boulevard, BLOK CE/A1 No.5 📲 Hubungi kami sekarang!",
        "tag": "promo"
    },
    "edukasi": {
        "text": "💧 Tips Perawatan Terbaik: Pisahkan warna terang & gelap, jangan overload mesin. Laundry profesional = hasil terbaik! 🧺 Kami siap bantu Anda! Hubungi: 0812-8878-3088",
        "tag": "edukasi"
    },
    "testimoni": {
        "text": "⭐ Pelanggan puas! 'Cuci di SIJI super cepat dan kualitas bagus. Selalu rapi & wangi!' Jadilah pelanggan setia kami. Pesan sekarang! 📲",
        "tag": "testimoni"
    }
}

def load_media():
    """Select random media file"""
    try:
        files = [f for f in os.listdir(MEDIA_PATH) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if files:
            selected = random.choice(files)
            logger.info(f"Selected media: {selected}")
            return selected
    except Exception as e:
        logger.error(f"Error loading media: {e}")
    return None

def create_enhanced_idea(caption_text, caption_tag, image_filename):
    """Create Buffer Idea with full metadata via GraphQL"""
    try:
        image_url = f"https://sijibintaro.id/media/{image_filename}"
        
        # Calculate scheduled date (next Sunday at 20:00 WIB = 13:00 UTC)
        today = datetime.utcnow()
        days_ahead = 6 - today.weekday()  # 0 = Monday, 6 = Sunday
        if days_ahead <= 0:
            days_ahead += 7
        next_sunday = today + timedelta(days=days_ahead)
        scheduled_date = next_sunday.replace(hour=13, minute=0, second=0, microsecond=0).isoformat() + "Z"
        
        graphql_query = {
            "query": """mutation CreateIdea($orgId: ID!, $title: String!, $text: String!, $imageUrl: String!, $altText: String!, $tags: [TagInput!]!, $services: [Service!]!, $scheduledDate: String!) {
              createIdea(input: {
                organizationId: $orgId
                content: {
                  title: $title
                  text: $text
                  media: [
                    {
                      url: $imageUrl
                      alt: $altText
                      type: image
                    }
                  ]
                  tags: $tags
                  aiAssisted: true
                  services: $services
                  date: $scheduledDate
                }
              }) {
                ... on Idea {
                  id
                  content {
                    title
                    text
                    media {
                      url
                      alt
                      type
                    }
                    tags {
                      name
                      color
                    }
                    aiAssisted
                    services
                    date
                  }
                }
                ... on MutationError {
                  message
                }
              }
            }""",
            "variables": {
                "orgId": BUFFER_ORG_ID,
                "title": "SIJI Weekly Post",
                "text": caption_text,
                "imageUrl": image_url,
                "altText": f"SIJI Laundry Service - {caption_tag.title()}",
                "tags": [
                    {"id": caption_tag, "name": caption_tag.title(), "color": "#FF6B6B"}
                ],
                "services": ["INSTAGRAM", "TIKTOK"],
                "scheduledDate": scheduled_date
            }
        }
        
        headers = {
            "Authorization": f"Bearer {BUFFER_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(BUFFER_API_URL, json=graphql_query, headers=headers, timeout=30)
        
        # Check for non-recoverable errors
        if response.status_code != 200:
            logger.error(f"HTTP {response.status_code}: {response.text[:200]}")
            return False
        
        data = response.json()
        
        # Handle GraphQL errors
        if "errors" in data:
            for err in data["errors"]:
                error_code = err.get("extensions", {}).get("code", "UNKNOWN")
                logger.error(f"GraphQL error [{error_code}]: {err.get('message')}")
            return False
        
        # Handle response
        if "data" in data:
            result = data["data"]["createIdea"]
            
            if result and "id" in result:
                idea_id = result["id"]
                content = result["content"]
                logger.info(f"✅ Idea created - ID: {idea_id}")
                logger.info(f"   Title: {content['title']}")
                logger.info(f"   Type: {content['tags'][0]['name'] if content['tags'] else 'untagged'}")
                logger.info(f"   Scheduled: {content['date']}")
                logger.info(f"   Services: {', '.join(content['services'])}")
                logger.info(f"   AI-Assisted: {content['aiAssisted']}")
                logger.info(f"   Media: {len(content['media'])} asset(s)")
                return True
            
            elif result and "message" in result:
                logger.error(f"Buffer error: {result['message']}")
                return False
        
        logger.error("Unexpected response format")
        return False
        
    except Exception as e:
        logger.error(f"Exception: {e}")
        return False

def main():
    logger.info("="*70)
    logger.info("SIJI Buffer Auto-Post Enhanced — Starting")
    logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
    
    # Validate tokens
    if not BUFFER_API_TOKEN or not BUFFER_IG_ID or not BUFFER_TIKTOK_ID:
        logger.error("Missing required env vars (token, IG ID, TikTok ID)")
        return 1
    
    # Generate caption
    caption_type = random.choice(list(CAPTION_TEMPLATES.keys()))
    caption_data = CAPTION_TEMPLATES[caption_type]
    logger.info(f"Caption type: {caption_type}")
    logger.info(f"Caption: {caption_data['text']}")
    
    # Load media
    logger.info("Loading media...")
    image_file = load_media()
    if not image_file:
        logger.error("No media file found")
        return 1
    
    # Create enhanced Idea
    logger.info("\nCreating Idea with full metadata...")
    if create_enhanced_idea(caption_data['text'], caption_data['tag'], image_file):
        logger.info("="*70)
        logger.info("✅ SIJI Buffer Auto-Post Enhanced — SUCCESS")
        logger.info("📍 Idea created with: caption, media, tags, services, scheduling date, AI flag")
        return 0
    else:
        logger.error("="*70)
        logger.error("❌ SIJI Buffer Auto-Post Enhanced — FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
