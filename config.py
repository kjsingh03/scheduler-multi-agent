import os
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class SystemConfig:
    # Redis Configuration
    redis_host: str = os.getenv('REDIS_HOST', 'localhost')
    redis_port: int = int(os.getenv('REDIS_PORT', '6379'))
    redis_db: int = int(os.getenv('REDIS_DB', '0'))
    
    # API Configuration
    api_host: str = os.getenv('API_HOST', '0.0.0.0')
    api_port: int = int(os.getenv('API_PORT', '8000'))
    
    # Logging Configuration
    log_level: str = os.getenv('LOG_LEVEL', 'INFO')
    log_file: str = os.getenv('LOG_FILE', 'logs/multi_agent_system.log')
    
    # Agent Configuration
    max_retries: int = int(os.getenv('MAX_RETRIES', '3'))
    message_timeout: int = int(os.getenv('MESSAGE_TIMEOUT', '30'))
    
    # Schedule Configuration
    default_timezone: str = os.getenv('TIMEZONE', 'UTC')
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'redis': {
                'host': self.redis_host,
                'port': self.redis_port,
                'db': self.redis_db
            },
            'api': {
                'host': self.api_host,
                'port': self.api_port
            },
            'logging': {
                'level': self.log_level,
                'file': self.log_file
            },
            'agents': {
                'max_retries': self.max_retries,
                'message_timeout': self.message_timeout
            },
            'scheduling': {
                'timezone': self.default_timezone
            }
        }

# Load configuration
config = SystemConfig()
