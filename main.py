import asyncio
import logging
import uuid
import os
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configure logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/multi_agent_system.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Data Models
@dataclass
class Message:
    id: str
    sender_id: str
    receiver_id: str
    content: Dict
    timestamp: datetime
    message_type: str

class AgentType(Enum):
    DATA_PROCESSOR = "data_processor"
    ANALYZER = "analyzer"
    NOTIFIER = "notifier"

# Communication Manager
class CommunicationManager:
    def __init__(self):
        self.agent_queues: Dict[str, asyncio.Queue] = {}
        self.message_history: List[Message] = []
    
    def register_agent(self, agent_id: str):
        if agent_id not in self.agent_queues:
            self.agent_queues[agent_id] = asyncio.Queue()
            logger.info(f"Registered agent: {agent_id}")
    
    async def send_message(self, message: Message):
        if message.receiver_id in self.agent_queues:
            await self.agent_queues[message.receiver_id].put(message)
            self.message_history.append(message)
            logger.info(f"Message sent from {message.sender_id} to {message.receiver_id}")
            return True
        logger.warning(f"Agent {message.receiver_id} not found")
        return False
    
    async def get_message(self, agent_id: str) -> Optional[Message]:
        try:
            if agent_id in self.agent_queues:
                return await asyncio.wait_for(self.agent_queues[agent_id].get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None

# Base Agent
class BaseAgent:
    def __init__(self, agent_id: str, agent_type: AgentType, comm_manager: CommunicationManager):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.comm_manager = comm_manager
        self.is_running = False
        self.comm_manager.register_agent(agent_id)
    
    async def start(self):
        self.is_running = True
        logger.info(f"Agent {self.agent_id} ({self.agent_type.value}) started")
        asyncio.create_task(self.message_listener())
    
    async def stop(self):
        self.is_running = False
        logger.info(f"Agent {self.agent_id} stopped")
    
    async def message_listener(self):
        while self.is_running:
            message = await self.comm_manager.get_message(self.agent_id)
            if message:
                await self.handle_message(message)
            await asyncio.sleep(0.1)
    
    async def handle_message(self, message: Message):
        logger.info(f"Agent {self.agent_id} received message from {message.sender_id}: {message.message_type}")
    
    async def send_message(self, receiver_id: str, content: Dict, message_type: str = "general"):
        message = Message(
            id=str(uuid.uuid4()),
            sender_id=self.agent_id,
            receiver_id=receiver_id,
            content=content,
            timestamp=datetime.now(),
            message_type=message_type
        )
        return await self.comm_manager.send_message(message)

# Specific Agents
class DataProcessorAgent(BaseAgent):
    def __init__(self, agent_id: str, comm_manager: CommunicationManager):
        super().__init__(agent_id, AgentType.DATA_PROCESSOR, comm_manager)
        self.processed_records = 0
    
    async def handle_message(self, message: Message):
        if message.message_type == 'process_data':
            await self.process_data(message.content)
    
    async def process_data(self, data: Dict):
        try:
            logger.info(f"Agent {self.agent_id}: Processing {len(data.get('records', []))} records")
            await asyncio.sleep(1)  # Simulate processing
            self.processed_records += len(data.get('records', []))
            processed_data = {
                "processed_at": datetime.now().isoformat(),
                "results": {"record_count": len(data.get("records", [])), "total_processed": self.processed_records}
            }
            await self.send_message("analyzer_001", processed_data, "data_processed")
        except asyncio.CancelledError:
            logger.info(f"Agent {self.agent_id}: Processing cancelled")
            raise
    
    async def scheduled_data_fetch(self):
        logger.info(f"Agent {self.agent_id}: Running data fetch")
        mock_data = {
            "records": [{"id": i, "value": i*10} for i in range(5)]
        }
        await self.process_data(mock_data)

class AnalyzerAgent(BaseAgent):
    def __init__(self, agent_id: str, comm_manager: CommunicationManager):
        super().__init__(agent_id, AgentType.ANALYZER, comm_manager)
        self.analyses_completed = 0
    
    async def handle_message(self, message: Message):
        if message.message_type == 'data_processed':
            await self.analyze_data(message.content)
    
    async def analyze_data(self, data: Dict):
        logger.info(f"Agent {self.agent_id}: Analyzing {data.get('results', {}).get('record_count', 0)} records")
        await asyncio.sleep(1)
        self.analyses_completed += 1
        insights = {
            "analysis_id": str(uuid.uuid4()),
            "confidence": round(0.85 + (self.analyses_completed % 10) * 0.01, 2)
        }
        if insights["confidence"] > 0.8:  # Ensures notifications
            await self.send_message("notifier_001", insights, "analysis_complete")

class NotifierAgent(BaseAgent):
    def __init__(self, agent_id: str, comm_manager: CommunicationManager):
        super().__init__(agent_id, AgentType.NOTIFIER, comm_manager)
        self.notifications_sent = 0
    
    async def handle_message(self, message: Message):
        if message.message_type == 'analysis_complete':
            await self.send_notification(message.content)
    
    async def send_notification(self, data: Dict):
        logger.info(f"Agent {self.agent_id}: Sending notification, confidence {data.get('confidence', 0)*100:.1f}%")
        self.notifications_sent += 1

# Main System
class MultiAgentSystem:
    def __init__(self):
        self.comm_manager = CommunicationManager()
        self.scheduler = AsyncIOScheduler(timezone='UTC')
        self.agents: Dict[str, BaseAgent] = {}
    
    async def initialize_agents(self):
        self.agents = {
            "processor_001": DataProcessorAgent("processor_001", self.comm_manager),
            "analyzer_001": AnalyzerAgent("analyzer_001", self.comm_manager),
            "notifier_001": NotifierAgent("notifier_001", self.comm_manager)
        }
        for agent in self.agents.values():
            await agent.start()
    
    def setup_schedules(self):
        self.scheduler.add_job(self.agents["processor_001"].scheduled_data_fetch, 'interval', seconds=10)
        logger.info("Scheduled data fetch every 10 seconds")
    
    async def start_system(self):
        logger.info("Starting Multi-Agent System...")
        await self.initialize_agents()
        self.setup_schedules()
        self.scheduler.start()
        logger.info("System started")
    
    async def stop_system(self):
        logger.info("Stopping Multi-Agent System...")
        self.scheduler.pause()  # Prevent new jobs
        self.scheduler.shutdown(wait=True)  # Wait for running jobs
        for agent in self.agents.values():
            await agent.stop()
        logger.info("System stopped")

# Demo
async def demo_system():
    system = MultiAgentSystem()
    try:
        await system.start_system()
        logger.info("System running. Press Ctrl+C to stop...")
        while True:
            await asyncio.sleep(10)  # Periodically log message count
            logger.info(f"Total messages exchanged: {len(system.comm_manager.message_history)}")
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        await system.stop_system()

if __name__ == "__main__":
    asyncio.run(demo_system())