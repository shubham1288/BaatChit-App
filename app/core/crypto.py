import logging
from cryptography.fernet import Fernet
from app.core.config import settings

logger = logging.getLogger(__name__)

def get_fernet():
    try:
        return Fernet(settings.message_encryption_key.encode())
    except Exception as e:
        logger.error("Failed to initialize Fernet with key: %s", e)
        raise

def encrypt_content(content: str) -> str:
    if not content:
        return ""
    try:
        f = get_fernet()
        return f.encrypt(content.encode()).decode()
    except Exception as e:
        logger.error("Encryption failed: %s", e)
        return content # Fallback to plain text if encryption fails to prevent crash

def decrypt_content(encrypted_content: str) -> str:
    if not encrypted_content:
        return ""
    try:
        f = get_fernet()
        return f.decrypt(encrypted_content.encode()).decode()
    except Exception as e:
        # Fallback if content is not encrypted or key is wrong
        logger.warning("Decryption failed for content (returning original): %s", e)
        return encrypted_content
