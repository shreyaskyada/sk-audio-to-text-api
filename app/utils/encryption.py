import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging

logger = logging.getLogger(__name__)

class EncryptionService:
    """Service for encrypting and decrypting data for HIPAA compliance"""
    
    def __init__(self):
        self.encryption_key = self._get_or_create_key()
        try:
            self.cipher_suite = Fernet(self.encryption_key)
        except ValueError as e:
            logger.error(f"Invalid encryption key: {str(e)}")
            # Generate a new valid key
            self.encryption_key = Fernet.generate_key()
            self.cipher_suite = Fernet(self.encryption_key)
            logger.info("Generated new valid encryption key")
    
    def _get_or_create_key(self) -> bytes:
        """Get or create encryption key"""
        key_string = os.getenv('ENCRYPTION_KEY')
        
        if not key_string:
            logger.warning("No encryption key found in environment. Generating new key.")
            # Generate a new key (in production, this should be done securely)
            key = Fernet.generate_key()
            logger.info(f"Generated new encryption key: {key.decode()}")
            return key
        
        # If key is provided as string, convert to bytes
        if isinstance(key_string, str):
            try:
                # Try to decode as base64 first
                decoded_key = base64.urlsafe_b64decode(key_string.encode())
                if len(decoded_key) == 32:  # Valid Fernet key length
                    return key_string.encode()
                else:
                    raise ValueError("Invalid key length")
            except Exception:
                # If not valid base64, derive key from password
                password = key_string.encode()
                salt = b'salt_12345678901234567890'  # In production, use random salt
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(password))
                return key
        
        return key_string
    
    def encrypt(self, data: bytes) -> bytes:
        """Encrypt data"""
        try:
            if isinstance(data, str):
                data = data.encode('utf-8')
            
            encrypted_data = self.cipher_suite.encrypt(data)
            return encrypted_data
            
        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            raise
    
    def decrypt(self, encrypted_data: bytes) -> bytes:
        """Decrypt data"""
        try:
            if isinstance(encrypted_data, str):
                encrypted_data = encrypted_data.encode('utf-8')
            
            decrypted_data = self.cipher_suite.decrypt(encrypted_data)
            return decrypted_data
            
        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            raise
    
    def encrypt_string(self, text: str) -> str:
        """Encrypt string and return base64 encoded result"""
        try:
            encrypted_bytes = self.encrypt(text.encode('utf-8'))
            return base64.urlsafe_b64encode(encrypted_bytes).decode('utf-8')
            
        except Exception as e:
            logger.error(f"String encryption failed: {str(e)}")
            raise
    
    def decrypt_string(self, encrypted_text: str) -> str:
        """Decrypt base64 encoded string"""
        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_text.encode('utf-8'))
            decrypted_bytes = self.decrypt(encrypted_bytes)
            return decrypted_bytes.decode('utf-8')
            
        except Exception as e:
            logger.error(f"String decryption failed: {str(e)}")
            raise
    
    def verify_keys(self) -> bool:
        """Verify that encryption keys are properly configured"""
        try:
            # Test encryption/decryption
            test_data = b"test_data_for_encryption"
            encrypted = self.encrypt(test_data)
            decrypted = self.decrypt(encrypted)
            
            return test_data == decrypted
            
        except Exception as e:
            logger.error(f"Key verification failed: {str(e)}")
            return False
    
    def get_key_info(self) -> dict:
        """Get information about the encryption key (for debugging)"""
        return {
            "key_length": len(self.encryption_key),
            "key_type": type(self.encryption_key).__name__,
            "cipher_suite_available": self.cipher_suite is not None
        }

