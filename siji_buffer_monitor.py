#!/usr/bin/env python3
"""
SIJI Buffer Monitor
Daily monitoring: health checks + Ideas→Posts tracking
"""

import os, sys, requests, json, logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv('.env.buffer')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('logs/buffer_monitor.log'), logging.StreamHandler()]
)
logger = logging.getLogger()

BUFFER_API_TOKEN = os.getenv("BUFFER_API_TOKEN", "").strip()
BUFFER_API_URL = "https://api.buffer.com"
BUFFER_ORG_ID = "69b39c8a4eae0eec66762adc"
BUFFER_IG_ID = os.getenv("BUFFER_IG_ID", "").strip()
BUFFER_TIKTOK_ID = os.getenv("BUFFER_TIKTOK_ID", "").strip()

def query_buffer(graphql_query, variables):
    """Generic GraphQL query to Buffer API"""
    try:
        headers = {
            "Authorization": f"Bearer {BUFFER_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            BUFFER_API_URL,
            json={"query": graphql_query, "variables": variables},
            headers=headers,
            timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"HTTP {response.status_code}")
            return None
        
        data = response.json()
        if "errors" in data:
            logger.error(f"GraphQL error: {data['errors']}")
            return None
        
        return data.get("data")
    
    except Exception as e:
        logger.error(f"Exception: {e}")
        return None

def check_channel_health():
    """Check if channels are connected and queue is active"""
    query = """
    query GetChannels($orgId: ID!) {
      channels(input: {organizationId: $orgId}) {
        edges {
          node {
            id
            name
            service
            isDisconnected
            isQueuePaused
          }
        }
      }
    }
    """
    
    data = query_buffer(query, {"orgId": BUFFER_ORG_ID})
    if not data:
        return False
    
    logger.info("Channel Health Check:")
    for edge in data.get("channels", {}).get("edges", []):
        node = edge["node"]
        status = "✅" if not node["isDisconnected"] else "❌"
        queue = "⏸️ PAUSED" if node["isQueuePaused"] else "🟢 ACTIVE"
        logger.info(f"  {status} {node['name']} ({node['service']}) - Queue: {queue}")
        
        if node["isDisconnected"]:
            logger.warning(f"    ⚠️ DISCONNECTED - reconnect needed")
        if node["isQueuePaused"]:
            logger.warning(f"    ⚠️ QUEUE PAUSED - may need to resume")
    
    return True

def check_org_limits():
    """Check organization posting limits"""
    query = """
    query GetOrgLimits($orgId: ID!) {
      organization(id: $orgId) {
        limits {
          scheduledPosts
          ideas
          generateContent
        }
      }
    }
    """
    
    data = query_buffer(query, {"orgId": BUFFER_ORG_ID})
    if not data:
        return False
    
    limits = data.get("organization", {}).get("limits", {})
    logger.info("Organization Limits:")
    logger.info(f"  Scheduled posts limit: {limits.get('scheduledPosts')}")
    logger.info(f"  Ideas limit: {limits.get('ideas')}")
    logger.info(f"  AI generation credits: {limits.get('generateContent')}")
    
    return True

def check_scheduled_posts():
    """Check what posts are scheduled"""
    query = """
    query GetScheduled($orgId: ID!) {
      posts(input: {
        organizationId: $orgId
        filter: {
          status: [scheduled]
          channelIds: ["IG_ID", "TIKTOK_ID"]
        }
        sort: [{field: dueAt, direction: asc}]
      }) {
        edges {
          node {
            id
            text
            dueAt
            channelId
          }
        }
        pageInfo {
          hasNextPage
        }
      }
    }
    """
    
    # Note: This is a template - actual channel IDs would be substituted
    logger.info("Scheduled Posts: (template query - requires channel IDs)")
    logger.info("  Would query posts scheduled for future publishing")
    
    return True

def main():
    logger.info("="*70)
    logger.info("SIJI Buffer Monitor — Starting Daily Health Check")
    logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
    
    # Check token
    if not BUFFER_API_TOKEN:
        logger.error("BUFFER_API_TOKEN not set")
        return 1
    
    # Run checks
    logger.info("\n📊 Running health checks...\n")
    
    check_channel_health()
    logger.info("")
    
    check_org_limits()
    logger.info("")
    
    check_scheduled_posts()
    
    logger.info("="*70)
    logger.info("✅ SIJI Buffer Monitor — Health Check Complete")
    return 0

if __name__ == "__main__":
    sys.exit(main())
