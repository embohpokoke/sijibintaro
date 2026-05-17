#!/usr/bin/env python3
"""
SIJI Buffer Auto-Post (GraphQL Ideas)
Weekly automated caption generation → Buffer Ideas (drafts)
Follows Buffer's GraphQL API standards
"""

import os, sys, requests, json, logging, random
from datetime import datetime
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
MEDIA_PATH = "/opt/sijibintaro/media/"

CAPTIONS = {
    "promo": "🚀 Promo Minggu! Hemat cuci 08:00-16:00, bayar sekarang 25% ✨ Lokasi: Jl. Raya Emerald Boulevard, BLOK CE/A1 No.5 📲 Hubungi kami sekarang!",
    "edukasi": "💧 Tips Perawatan Terbaik: Pisahkan warna terang & gelap, jangan overload mesin. Laundry profesional = hasil terbaik! 🧺 Kami siap bantu Anda! Hubungi: 0812-8878-3088",
    "testimoni": "⭐ Pelanggan puas! 'Cuci di SIJI super cepat dan kualitas bagus. Selalu rapi & wangi!' Jadilah pelanggan setia kami. Pesan sekarang! 📲"
}

def load_media():
    """Select random media file for reference"""
    try:
        files = [f for f in os.listdir(MEDIA_PATH) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if files:
            selected = random.choice(files)
            logger.info(f"Selected media: {selected}")
            return os.path.join(MEDIA_PATH, selected)
    except Exception as e:
        logger.error(f"Error loading media: {e}")
    return None

def create_buffer_idea(caption, media_path=None):
    """Create Buffer Idea (draft) via GraphQL
    
    Follows Buffer's GraphQL API standards:
    - Always include ... on MutationError for future-proofing
    - Return typed data in payload (Idea object)
    - Handle non-recoverable errors in errors array
    """
    try:
        # GraphQL mutation with future-proof error handling
        graphql_query = {
            "query": """mutation CreateIdea($orgId: ID!, $title: String!, $text: String!) {
              createIdea(input: {
                organizationId: $orgId
                content: {
                  title: $title
                  text: $text
                }
              }) {
                ... on Idea {
                  id
                  content {
                    title
                    text
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
                "text": caption
            }
        }
        
        headers = {
            "Authorization": f"Bearer {BUFFER_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(BUFFER_API_URL, json=graphql_query, headers=headers, timeout=30)
        
        # Check for non-recoverable errors (errors array)
        if response.status_code != 200:
            logger.error(f"HTTP {response.status_code}: {response.text[:200]}")
            return False
        
        data = response.json()
        
        # Handle non-recoverable GraphQL errors
        if "errors" in data:
            errors = data["errors"]
            for err in errors:
                error_code = err.get("extensions", {}).get("code", "UNKNOWN")
                logger.error(f"GraphQL error [{error_code}]: {err.get('message')}")
            return False
        
        # Handle recoverable errors (typed errors in payload)
        if "data" in data:
            result = data["data"]["createIdea"]
            
            if result and "id" in result:
                # Success: Idea created
                idea_id = result["id"]
                logger.info(f"✅ Buffer Idea created - Idea ID: {idea_id}")
                logger.info(f"   Title: {result['content']['title']}")
                logger.info(f"   Text: {result['content']['text'][:60]}...")
                if media_path:
                    logger.info(f"   Media reference: {media_path}")
                logger.info(f"   → Go to Buffer.com to add image and post to IG/TikTok")
                return True
            
            elif result and "message" in result:
                # Recoverable error: MutationError with message
                logger.error(f"Buffer error: {result['message']}")
                return False
        
        logger.error("Unexpected response format")
        return False
        
    except Exception as e:
        logger.error(f"Exception: {e}")
        return False

def main():
    logger.info("="*70)
    logger.info("SIJI Buffer Auto-Post — Starting")
    logger.info(f"Timestamp: {datetime.now().isoformat()}")
    
    # Validate token
    if not BUFFER_API_TOKEN:
        logger.error("BUFFER_API_TOKEN not set in .env.buffer")
        return 1
    
    # Generate caption
    caption_type = random.choice(list(CAPTIONS.keys()))
    caption = CAPTIONS[caption_type]
    logger.info(f"Caption type: {caption_type}")
    logger.info(f"Caption: {caption}")
    
    # Load media reference
    logger.info("Loading media reference...")
    media_path = load_media()
    
    # Create Buffer Idea
    logger.info("\nCreating Buffer Idea (draft)...")
    if create_buffer_idea(caption, media_path):
        logger.info("="*70)
        logger.info("✅ SIJI Buffer Auto-Post — SUCCESS")
        logger.info("📍 Next: Go to Buffer.com → Drafts → Add image → Post to IG/TikTok")
        return 0
    else:
        logger.error("="*70)
        logger.error("❌ SIJI Buffer Auto-Post — FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
