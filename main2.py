import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from collections import defaultdict, deque
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from fastapi import FastAPI, Depends
from pydantic import BaseModel, field_validator
from dataclasses import dataclass
import uuid
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json
from contextlib import asynccontextmanager

# Setup logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/multi_agent.log'), 
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SystemConfig(BaseModel):
    api_host: str = os.getenv('API_HOST', '0.0.0.0')
    api_port: int = int(os.getenv('API_PORT', '8000'))
    extraction_interval: int = 30  # 30 seconds
    products_per_extraction: int = 2  # 2 products per extraction
    
    @field_validator('api_port')
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Port must be positive')
        return v

config = SystemConfig()

@dataclass
class Message:
    id: str
    sender_id: str
    receiver_id: str
    content: Dict
    timestamp: datetime
    message_type: str

class InMemoryCommunicationManager:
    """Simple in-memory message queue system to replace Redis"""
    
    def __init__(self):
        self.message_queues = defaultdict(deque)
        self.message_history = []
        
    async def send_message(self, message: Message):
        """Send a message to the target agent's queue"""
        self.message_queues[message.receiver_id].appendleft(message)
        self.message_history.append(message)
        logger.info(f"Message sent from {message.sender_id} to {message.receiver_id} | Type: {message.message_type}")
        
    async def get_message(self, agent_id: str) -> Optional[Message]:
        """Get the next message for an agent"""
        if self.message_queues[agent_id]:
            message = self.message_queues[agent_id].pop()
            logger.info(f"Message retrieved by {agent_id} from {message.sender_id}")
            return message
        return None
    
    def get_queue_status(self):
        """Get status of all message queues"""
        return {agent_id: len(queue) for agent_id, queue in self.message_queues.items()}

class BaseAgent:
    """Base class for all agents with messaging capabilities"""
    
    def __init__(self, agent_id: str, comm_manager: InMemoryCommunicationManager):
        self.agent_id = agent_id
        self.comm_manager = comm_manager
        self.is_running = False
        self.message_count = 0
        
    async def start(self):
        """Start the agent and begin listening for messages"""
        self.is_running = True
        logger.info(f"Agent {self.agent_id} started")
        asyncio.create_task(self.message_listener())
        
    async def stop(self):
        """Stop the agent"""
        self.is_running = False
        logger.info(f"Agent {self.agent_id} stopped")
        
    async def message_listener(self):
        """Continuously listen for incoming messages"""
        while self.is_running:
            try:
                message = await self.comm_manager.get_message(self.agent_id)
                if message:
                    self.message_count += 1
                    await self.handle_message(message)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Agent {self.agent_id} message listener error: {str(e)}")
                await asyncio.sleep(1)
            
    async def handle_message(self, message: Message):
        """Override this method in subclasses to handle specific message types"""
        logger.info(f"{self.agent_id} received message: {message.message_type}")
        
    async def send_message(self, receiver_id: str, content: Dict, message_type: str = "general"):
        """Send a message to another agent"""
        message = Message(
            id=str(uuid.uuid4()),
            sender_id=self.agent_id,
            receiver_id=receiver_id,
            content=content,
            timestamp=datetime.now(),
            message_type=message_type
        )
        await self.comm_manager.send_message(message)

class ExtractorAgent(BaseAgent):
    """Agent responsible for web scraping and data extraction"""
    
    def __init__(self, agent_id: str, comm_manager: InMemoryCommunicationManager):
        super().__init__(agent_id, comm_manager)
        self.extraction_count = 0
        self.categories = ["phones", "tablets", "wearables"]
        self.current_category_index = 0
        
    async def scheduled_fetch(self):
        """Fetch product data from AT&T website - rotates through categories"""
        category = self.categories[self.current_category_index]
        self.current_category_index = (self.current_category_index + 1) % len(self.categories)
        
        print(f"\n{'='*60}")
        print(f"🔄 SWITCHING TO CATEGORY: {category.upper()}")
        print(f"{'='*60}")
        
        url = f"https://www.att.com/buy/{category}/"
        logger.info(f"ExtractorAgent: Starting extraction from {url} (Target: {config.products_per_extraction} products)")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                # Set user agent to avoid blocking
                await page.set_extra_http_headers({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                })
                
                await page.goto(url, wait_until='networkidle', timeout=30000)
                
                # Wait for products to load
                await page.wait_for_selector('div[data-testid*="plp-item-card"]', timeout=10000)
                
                html = await page.content()
                await browser.close()
                
            await self._parse_products(html, category)
            
        except Exception as e:
            print(f"❌ EXTRACTION ERROR for {category}: {str(e)}")
            logger.error(f"ExtractorAgent: Extraction error for {category}: {str(e)}")
            await self.send_message("formatter_001", {
                "error": str(e), 
                "category": category,
                "records": []
            }, "extraction_error")
            
    async def _parse_products(self, html: str, category: str):
        """Parse HTML content to extract product information"""
        soup = BeautifulSoup(html, 'html.parser')
        products = []
        
        # Find product cards using multiple possible selectors
        selectors = [
            'div[data-testid*="plp-item-card-sku"]',
            'div[data-testid*="plp-item-card"]',
            '.plp-item-card',
            '[data-testid*="product-card"]'
        ]
        
        product_cards = []
        for selector in selectors:
            product_cards = soup.select(selector)
            if product_cards:
                logger.info(f"ExtractorAgent: Found {len(product_cards)} product cards using selector: {selector}")
                break
        
        if not product_cards:
            print(f"⚠️  NO PRODUCTS FOUND for {category}")
            logger.warning(f"ExtractorAgent: No product cards found for {category}")
            await self.send_message("formatter_001", {
                "records": [],
                "category": category,
                "extraction_count": 0
            }, "extracted_data")
            return
        
        # Limit to the configured number of products per extraction
        product_cards = product_cards[:config.products_per_extraction]
        
        for i, card in enumerate(product_cards):
            try:
                # Extract product ID
                card_testid = card.get('data-testid', '')
                product_id = card_testid.replace('plp-item-card-', '') if 'plp-item-card-' in card_testid else f'product_{category}_{i}'
                
                # Extract product link
                anchor = card.select_one('a')
                href = anchor.get('href') if anchor else None
                product_url = f"https://www.att.com{href}" if href and href.startswith('/') else href
                
                # Extract brand
                brand_selectors = ['.type-sm', '.brand', '[data-testid*="brand"]', 'h4', '.product-brand']
                brand = "Unknown"
                for selector in brand_selectors:
                    brand_elem = card.select_one(selector)
                    if brand_elem and brand_elem.get_text(strip=True):
                        brand = brand_elem.get_text(strip=True)
                        break
                
                # Extract product name
                name_selectors = ['h3.heading-xs', 'h3', '.product-name', '[data-testid*="name"]', '.heading-sm']
                name = "Unknown Product"
                for selector in name_selectors:
                    name_elem = card.select_one(selector)
                    if name_elem and name_elem.get_text(strip=True):
                        name = name_elem.get_text(strip=True)
                        break
                
                # Extract price
                price_selectors = ['.heading-sm', '.price', '[data-testid*="price"]', '.product-price']
                price = 0.0
                for selector in price_selectors:
                    price_elem = card.select_one(selector)
                    if price_elem:
                        price_text = price_elem.get_text(strip=True)
                        import re
                        price_match = re.search(r'\$(\d+\.?\d*)', price_text)
                        if price_match:
                            price = float(price_match.group(1))
                            break
                
                # Extract rating
                rating_selectors = ['.star-rating-text .font-bold', '.rating', '[data-testid*="rating"]']
                rating = "N/A"
                for selector in rating_selectors:
                    rating_elem = card.select_one(selector)
                    if rating_elem and rating_elem.get_text(strip=True):
                        rating = rating_elem.get_text(strip=True)
                        break
                
                # Extract review count
                review_selectors = ['.star-rating-text .span-c', '.review-count', '[data-testid*="review"]']
                review_count = "0"
                for selector in review_selectors:
                    review_elem = card.select_one(selector)
                    if review_elem and review_elem.get_text(strip=True):
                        review_count = review_elem.get_text(strip=True)
                        break
                
                # Extract color
                color_selectors = ['[data-selected-color="true"]', '.color', '[data-testid*="color"]']
                color = "Default"
                for selector in color_selectors:
                    color_elem = card.select_one(selector)
                    if color_elem and color_elem.get_text(strip=True):
                        color = color_elem.get_text(strip=True)
                        break
                
                # Create product data
                product_data = {
                    "id": product_id,
                    "brand": brand,
                    "name": name,
                    "price": price,
                    "rating": rating,
                    "review_count": review_count,
                    "color": color,
                    "category": category,
                    "url": product_url or f"https://www.att.com/buy/{category}/",
                    "extraction_timestamp": datetime.now().isoformat(),
                    "extraction_sequence": self.extraction_count + len(products) + 1
                }
                
                products.append(product_data)
                print(f"📱 EXTRACTED: {brand} {name} - ${price}")
                    
            except Exception as e:
                logger.warning(f"ExtractorAgent: Error parsing product card {i}: {str(e)}")
                continue
        
        self.extraction_count += len(products)
        print(f"\n✅ EXTRACTION COMPLETE: {len(products)} products from {category.upper()}")
        print(f"📊 TOTAL EXTRACTED SO FAR: {self.extraction_count}")
        logger.info(f"ExtractorAgent: Successfully extracted {len(products)} products from {category}")
        
        # Send extracted data to FormatterAgent
        await self.send_message("formatter_001", {
            "records": products,
            "category": category,
            "extraction_count": len(products),
            "total_extracted": self.extraction_count
        }, "extracted_data")

class FormatterAgent(BaseAgent):
    """Agent responsible for data formatting and validation"""
    
    def __init__(self, agent_id: str, comm_manager: InMemoryCommunicationManager):
        super().__init__(agent_id, comm_manager)
        self.processed_count = 0
        
    async def handle_message(self, message: Message):
        """Handle incoming messages from ExtractorAgent"""
        await super().handle_message(message)
        
        if message.message_type == "extracted_data":
            await self._format_extracted_data(message.content)
        elif message.message_type == "extraction_error":
            await self._handle_extraction_error(message.content)
            
    async def _format_extracted_data(self, data: Dict):
        """Format and validate extracted product data"""
        records = data.get("records", [])
        category = data.get("category", "unknown")
        
        print(f"\n🔧 FORMATTER: Processing {len(records)} records for {category.upper()}")
        logger.info(f"FormatterAgent: Processing {len(records)} records for {category}")
        
        if not records:
            print(f"⚠️  FORMATTER: No records to process for {category.upper()}")
            logger.warning(f"FormatterAgent: No records to process for {category}")
            await self.send_message("display_001", {
                "records": [],
                "category": category,
                "processed_count": 0,
                "original_count": 0
            }, "formatted_data")
            return
        
        formatted_records = []
        
        for record in records:
            try:
                # Format and validate data
                formatted_record = {
                    "id": record.get("id", "unknown"),
                    "brand": record.get("brand", "Unknown").title(),
                    "name": self._clean_product_name(record.get("name", "Unknown Product")),
                    "price": round(float(record.get("price", 0)), 2),
                    "rating": self._format_rating(record.get("rating", "N/A")),
                    "review_count": self._format_review_count(record.get("review_count", "0")),
                    "color": record.get("color", "Default").title(),
                    "category": category.lower(),
                    "url": record.get("url"),
                    "formatted_timestamp": datetime.now().isoformat(),
                    "extraction_timestamp": record.get("extraction_timestamp"),
                    "extraction_sequence": record.get("extraction_sequence", 0),
                    "price_range": self._categorize_price(record.get("price", 0))
                }
                
                # Add computed fields
                formatted_record["display_price"] = f"${formatted_record['price']:.2f}"
                formatted_record["has_reviews"] = formatted_record["review_count"] > 0
                formatted_record["is_premium"] = formatted_record["price"] > 50
                formatted_record["brand_category"] = f"{formatted_record['brand']} {formatted_record['category'].title()}"
                
                formatted_records.append(formatted_record)
                print(f"✨ FORMATTED: {formatted_record['brand']} {formatted_record['name']} - ${formatted_record['price']}")
                
            except Exception as e:
                logger.warning(f"FormatterAgent: Error formatting record: {str(e)}")
                continue
        
        self.processed_count += len(formatted_records)
        print(f"✅ FORMATTING COMPLETE: {len(formatted_records)} products formatted")
        logger.info(f"FormatterAgent: Successfully formatted {len(formatted_records)} records (Total processed: {self.processed_count})")
        
        # Send formatted data to DisplayAgent
        await self.send_message("display_001", {
            "records": formatted_records,
            "category": category,
            "processed_count": len(formatted_records),
            "original_count": len(records),
            "total_processed": self.processed_count
        }, "formatted_data")
        
    async def _handle_extraction_error(self, data: Dict):
        """Handle extraction errors from ExtractorAgent"""
        print(f"❌ FORMATTER ERROR: {data.get('category')} - {data.get('error')}")
        logger.error(f"FormatterAgent: Received extraction error for {data.get('category')}: {data.get('error')}")
        
        # Send error notification to DisplayAgent
        await self.send_message("display_001", {
            "error": data.get("error"),
            "category": data.get("category"),
            "records": []
        }, "processing_error")
    
    def _clean_product_name(self, name: str) -> str:
        """Clean and standardize product names"""
        return name.strip().replace('\n', ' ').replace('\t', ' ')
    
    def _format_rating(self, rating: str) -> float:
        """Convert rating string to float"""
        try:
            return float(rating) if rating != "N/A" else 0.0
        except:
            return 0.0
    
    def _format_review_count(self, count: str) -> int:
        """Convert review count string to integer"""
        try:
            # Remove any non-numeric characters except commas
            import re
            clean_count = re.sub(r'[^\d,]', '', str(count))
            return int(clean_count.replace(',', '')) if clean_count else 0
        except:
            return 0
    
    def _categorize_price(self, price: float) -> str:
        """Categorize price into ranges"""
        if price == 0:
            return "Free"
        elif price < 20:
            return "Budget"
        elif price < 50:
            return "Mid-range"
        else:
            return "Premium"

class DisplayAgent(BaseAgent):
    """Agent responsible for data storage and API responses"""
    
    def __init__(self, agent_id: str, comm_manager: InMemoryCommunicationManager):
        super().__init__(agent_id, comm_manager)
        self.product_catalog = {}  # category -> [products]
        self.last_update = {}      # category -> timestamp
        self.error_log = []
        self.total_stored = 0
        
    async def handle_message(self, message: Message):
        """Handle incoming messages from FormatterAgent"""
        await super().handle_message(message)
        
        if message.message_type == "formatted_data":
            await self._store_formatted_data(message.content)
        elif message.message_type == "processing_error":
            await self._handle_processing_error(message.content)
            
    async def _store_formatted_data(self, data: Dict):
        """Store formatted product data"""
        records = data.get("records", [])
        category = data.get("category", "unknown")
        
        print(f"\n💾 DISPLAY: Storing {len(records)} products for {category.upper()}")
        logger.info(f"DisplayAgent: Storing {len(records)} products for {category}")
        
        if not records:
            print(f"⚠️  DISPLAY: No products to store for {category.upper()}")
            logger.warning(f"DisplayAgent: No products to store for {category}")
            return
        
        # Initialize category if not exists
        if category not in self.product_catalog:
            self.product_catalog[category] = []
        
        # Add new products to the category (append to maintain history)
        self.product_catalog[category].extend(records)
        self.last_update[category] = datetime.now()
        self.total_stored += len(records)
        
        # Keep only the latest 50 products per category to prevent memory issues
        if len(self.product_catalog[category]) > 50:
            self.product_catalog[category] = self.product_catalog[category][-50:]
        
        # Log statistics
        total_products = sum(len(products) for products in self.product_catalog.values())
        
        print(f"\n📋 CURRENT CATALOG STATUS:")
        print(f"   📱 Category: {category.upper()}")
        print(f"   📊 Products in this category: {len(self.product_catalog[category])}")
        print(f"   🏪 Total products in catalog: {total_products}")
        print(f"   📂 Total categories: {len(self.product_catalog)}")
        
        print(f"\n🛍️  PRODUCTS STORED IN {category.upper()}:")
        for i, product in enumerate(records, 1):
            print(f"   {i}. {product['brand']} {product['name']} - ${product['price']}")
        
        logger.info(f"DisplayAgent: Stored {len(records)} new products. Total in catalog: {total_products} across {len(self.product_catalog)} categories")
        
        print(f"\n{'='*60}")
        print(f"⏰ WAITING FOR NEXT CATEGORY SWITCH IN 30 SECONDS...")
        print(f"{'='*60}")
        
    async def _handle_processing_error(self, data: Dict):
        """Handle processing errors"""
        error_info = {
            "category": data.get("category"),
            "error": data.get("error"),
            "timestamp": datetime.now().isoformat()
        }
        self.error_log.append(error_info)
        
        # Keep only the latest 20 errors
        if len(self.error_log) > 20:
            self.error_log = self.error_log[-20:]
            
        print(f"❌ DISPLAY ERROR: {data.get('category')} - {data.get('error')}")
        logger.error(f"DisplayAgent: Logged error for {data.get('category')}: {data.get('error')}")
    
    def get_products_by_category(self, category: str) -> List[Dict]:
        """Get products for a specific category"""
        return self.product_catalog.get(category.lower(), [])
    
    def get_all_products(self) -> List[Dict]:
        """Get all products from all categories"""
        all_products = []
        for products in self.product_catalog.values():
            all_products.extend(products)
        return sorted(all_products, key=lambda x: x.get('extraction_timestamp', ''), reverse=True)
    
    def get_latest_products(self, limit: int = 10) -> List[Dict]:
        """Get the latest products across all categories"""
        all_products = self.get_all_products()
        return all_products[:limit]
    
    def get_catalog_stats(self) -> Dict:
        """Get catalog statistics"""
        stats = {
            "total_categories": len(self.product_catalog),
            "total_products": sum(len(products) for products in self.product_catalog.values()),
            "total_stored_lifetime": self.total_stored,
            "categories": {},
            "last_updates": {},
            "recent_activity": len([p for products in self.product_catalog.values() for p in products if (datetime.now() - datetime.fromisoformat(p.get('formatted_timestamp', '2000-01-01T00:00:00'))).seconds < 300])
        }
        
        for category, products in self.product_catalog.items():
            if products:
                prices = [p["price"] for p in products if p["price"] > 0]
                stats["categories"][category] = {
                    "product_count": len(products),
                    "avg_price": sum(prices) / len(prices) if prices else 0,
                    "price_range": f"${min(prices):.2f} - ${max(prices):.2f}" if prices else "N/A",
                    "latest_extraction": max(p.get('extraction_timestamp', '') for p in products)
                }
            
        for category, timestamp in self.last_update.items():
            stats["last_updates"][category] = timestamp.isoformat()
            
        return stats

class MultiAgentSystem:
    """Main system orchestrating all agents"""
    
    def __init__(self):
        self.comm_manager = InMemoryCommunicationManager()
        self.scheduler = AsyncIOScheduler(timezone='UTC')
        self.agents = {}
        self.is_running = False
        
    async def initialize_agents(self):
        """Initialize all agents"""
        print(f"\n{'='*60}")
        print("🚀 INITIALIZING MULTI-AGENT SYSTEM")
        print(f"{'='*60}")
        print("⚙️  Configuration:")
        print(f"   ⏱️  Extraction Interval: {config.extraction_interval} seconds")
        print(f"   📦 Products per extraction: {config.products_per_extraction}")
        print(f"   📂 Categories: phones, tablets, wearables")
        print(f"{'='*60}")
        
        logger.info("Initializing Multi-Agent System...")
        
        self.agents = {
            "extractor_001": ExtractorAgent("extractor_001", self.comm_manager),
            "formatter_001": FormatterAgent("formatter_001", self.comm_manager),
            "display_001": DisplayAgent("display_001", self.comm_manager)
        }
        
        # Start all agents
        for agent_id, agent in self.agents.items():
            await agent.start()
            print(f"✅ {agent_id.upper()} - {type(agent).__name__} started")
            
        print(f"{'='*60}")
        print("🎯 ALL AGENTS INITIALIZED AND READY!")
        print(f"{'='*60}")
        logger.info("All agents initialized and started")
        
    def setup_schedules(self):
        """Setup scheduled tasks for data extraction"""
        # Single job that rotates through categories automatically
        self.scheduler.add_job(
            func=lambda: asyncio.create_task(self.agents["extractor_001"].scheduled_fetch()),
            trigger='interval',
            seconds=config.extraction_interval,  # Every 30 seconds
            id="auto_extract",
            max_instances=1  # Prevent overlapping executions
        )
        
        self.scheduler.start()
        self.is_running = True
        
        print(f"\n🔄 SCHEDULER STARTED:")
        print(f"   ⏰ Every {config.extraction_interval} seconds")
        print(f"   🔀 Auto-rotating categories: phones → tablets → wearables")
        print(f"   📦 {config.products_per_extraction} products per extraction")
        print(f"{'='*60}")
        
        logger.info(f"Scheduled extraction job - {config.products_per_extraction} products every {config.extraction_interval} seconds")
        
    async def stop_system(self):
        """Stop the entire system"""
        logger.info("Stopping Multi-Agent System...")
        
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.is_running = False
        
        for agent in self.agents.values():
            await agent.stop()
            
        logger.info("Multi-Agent System stopped")
        
    def get_system_status(self) -> Dict:
        """Get overall system status"""
        return {
            "is_running": self.is_running,
            "extraction_config": {
                "interval_seconds": config.extraction_interval,
                "products_per_extraction": config.products_per_extraction
            },
            "agents": {
                agent_id: {
                    "is_running": agent.is_running,
                    "message_count": agent.message_count,
                    "type": type(agent).__name__,
                    "specific_stats": self._get_agent_specific_stats(agent)
                }
                for agent_id, agent in self.agents.items()
            },
            "message_queues": self.comm_manager.get_queue_status(),
            "scheduler_jobs": len(self.scheduler.get_jobs()) if self.scheduler.running else 0,
            "system_uptime": datetime.now().isoformat()
        }
    
    def _get_agent_specific_stats(self, agent) -> Dict:
        """Get agent-specific statistics"""
        if isinstance(agent, ExtractorAgent):
            return {"extraction_count": agent.extraction_count}
        elif isinstance(agent, FormatterAgent):
            return {"processed_count": agent.processed_count}
        elif isinstance(agent, DisplayAgent):
            return {"total_stored": agent.total_stored, "categories": len(agent.product_catalog)}
        return {}

# Global system instance
system = MultiAgentSystem()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"\n{'='*80}")
    print("🚀 MULTI-AGENT PRODUCT SCRAPER STARTING UP")
    print(f"{'='*80}")
    
    logger.info("Starting Multi-Agent Product Scraper API...")
    await system.initialize_agents()
    system.setup_schedules()
    
    print("🎉 SYSTEM IS NOW LIVE AND EXTRACTING!")
    print("📊 Check the terminal for real-time product extraction updates")
    print(f"{'='*80}")
    
    logger.info("Multi-Agent System is ready!")
    
    yield
    
    # Shutdown
    print(f"\n{'='*60}")
    print("🛑 SHUTTING DOWN MULTI-AGENT SYSTEM")
    print(f"{'='*60}")
    await system.stop_system()
    print("✅ SHUTDOWN COMPLETE")
    logger.info("Multi-Agent System shutdown complete")

# FastAPI application
app = FastAPI(
    title="Multi-Agent Product Scraper",
    description="A collaborative multi-agent system for scraping and processing product data",
    version="1.0.0",
    lifespan=lifespan
)

def get_display_agent() -> DisplayAgent:
    """Dependency to get the display agent"""
    return system.agents.get("display_001")

@app.get("/products/{category}")
async def get_products_by_category(
    category: str, 
    display_agent: DisplayAgent = Depends(get_display_agent)
):
    """Get products for a specific category"""
    if not display_agent:
        return {"error": "Display agent not available", "products": []}
    
    products = display_agent.get_products_by_category(category)
    return {
        "category": category,
        "products": products,
        "count": len(products),
        "last_update": display_agent.last_update.get(category.lower())
    }

@app.get("/products")
async def get_all_products(display_agent: DisplayAgent = Depends(get_display_agent)):
    """Get all products from all categories"""
    if not display_agent:
        return {"error": "Display agent not available", "products": []}
    
    products = display_agent.get_all_products()
    return {
        "products": products,
        "count": len(products),
        "stats": display_agent.get_catalog_stats()
    }

@app.get("/products/latest/{limit}")
async def get_latest_products(limit: int = 10, display_agent: DisplayAgent = Depends(get_display_agent)):
    """Get the latest products"""
    if not display_agent:
        return {"error": "Display agent not available", "products": []}
    
    products = display_agent.get_latest_products(limit)
    return {
        "products": products,
        "count": len(products),
        "limit": limit
    }

@app.get("/system/status")
async def get_system_status():
    """Get system status and agent information"""
    return system.get_system_status()

@app.get("/system/catalog-stats")
async def get_catalog_stats(display_agent: DisplayAgent = Depends(get_display_agent)):
    """Get detailed catalog statistics"""
    if not display_agent:
        return {"error": "Display agent not available"}
    
    return display_agent.get_catalog_stats()

@app.post("/system/extract")
async def trigger_manual_extraction():
    """Manually trigger extraction"""
    extractor = system.agents.get("extractor_001")
    if not extractor:
        return {"error": "Extractor agent not available"}
    
    # Trigger extraction in background
    asyncio.create_task(extractor.scheduled_fetch())
    return {"message": "Manual extraction triggered", "status": "started"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "system_running": system.is_running,
        "agents_count": len(system.agents),
        "config": {
            "extraction_interval": config.extraction_interval,
            "products_per_extraction": config.products_per_extraction
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host=config.api_host, 
        port=config.api_port,
        log_level="info"
    )