import os
import base64
import logging
from datetime import datetime
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy.orm import Session
from bot.db import DBUser, DBApiCredential, DBUserAllocation

def get_cipher() -> Fernet:
    """Obtiene el cifrador Fernet a partir del secreto de entorno para asegurar cifrado AES-256."""
    secret = os.getenv("ENCRYPTION_SECRET_KEY", "default_super_secret_dev_key_change_me_in_production")
    salt = b"enterprise_multiuser_salt"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return Fernet(key)

def encrypt_value(value: str) -> str:
    """Cifra un valor de texto plano a AES-256 base64."""
    if not value:
        return ""
    try:
        cipher = get_cipher()
        return cipher.encrypt(value.encode()).decode()
    except Exception as e:
        logging.error(f"Error al cifrar credencial: {e}")
        return ""

def decrypt_value(value: str) -> str:
    """Descifra un valor cifrado en AES-256 base64 a texto plano."""
    if not value:
        return ""
    try:
        cipher = get_cipher()
        return cipher.decrypt(value.encode()).decode()
    except Exception as e:
        logging.error(f"Error al descifrar credencial: {e}")
        return ""

class EnterpriseManager:
    def __init__(self, db_session: Session) -> None:
        self.db = db_session

    def create_user(self, email: str, name: str, role: str = "investor") -> DBUser:
        """Crea un nuevo usuario inversor o administrador en el holding."""
        existing = self.db.query(DBUser).filter(DBUser.email == email).first()
        if existing:
            return existing

        user = DBUser(
            email=email,
            name=name,
            role=role,
            status="active"
        )
        self.db.add(user)
        self.db.commit()
        logging.info(f"Usuario {name} ({email}) registrado exitosamente en el backoffice.")
        return user

    def add_api_credential(self, user_id: int, platform: str, api_key: str, api_secret: str, ibkr_client_id: int = None) -> DBApiCredential:
        """Agrega credenciales de API cifradas para un usuario."""
        enc_key = encrypt_value(api_key)
        enc_secret = encrypt_value(api_secret)
        
        credential = DBApiCredential(
            user_id=user_id,
            platform=platform,
            encrypted_api_key=enc_key,
            encrypted_api_secret=enc_secret,
            ibkr_client_id=ibkr_client_id,
            is_active=True
        )
        self.db.add(credential)
        self.db.commit()
        logging.info(f"Credencial {platform} agregada de forma segura para usuario ID: {user_id}.")
        return credential

    def get_api_credentials(self, user_id: int, platform: str) -> tuple[str, str, int | None]:
        """Obtiene y descifra las credenciales de un usuario específico."""
        cred = self.db.query(DBApiCredential).filter(
            DBApiCredential.user_id == user_id,
            DBApiCredential.platform == platform,
            DBApiCredential.is_active == True
        ).first()
        
        if not cred:
            return "", "", None
            
        api_key = decrypt_value(cred.encrypted_api_key)
        api_secret = decrypt_value(cred.encrypted_api_secret)
        return api_key, api_secret, cred.ibkr_client_id

    def set_user_allocation(self, user_id: int, symbol: str, allocated_pct: float, allocated_cap: float) -> DBUserAllocation:
        """Establece la distribución de capital (allocation) para un inversor."""
        alloc = self.db.query(DBUserAllocation).filter(
            DBUserAllocation.user_id == user_id,
            DBUserAllocation.symbol == symbol
        ).first()
        
        if not alloc:
            alloc = DBUserAllocation(user_id=user_id, symbol=symbol)
            self.db.add(alloc)
            
        alloc.allocated_pct = allocated_pct
        alloc.allocated_cap = allocated_cap
        self.db.commit()
        logging.info(f"Distribución de capital actualizada para usuario ID: {user_id} en {symbol}.")
        return alloc

    def get_active_users_credentials(self, platform: str) -> list[dict]:
        """Obtiene credenciales activas descifradas para todos los usuarios activos."""
        results = []
        active_users = self.db.query(DBUser).filter(DBUser.status == "active").all()
        user_ids = [u.id for u in active_users]
        
        if not user_ids:
            return []
            
        creds = self.db.query(DBApiCredential).filter(
            DBApiCredential.user_id.in_(user_ids),
            DBApiCredential.platform == platform,
            DBApiCredential.is_active == True
        ).all()
        
        for c in creds:
            user = next(u for u in active_users if u.id == c.user_id)
            results.append({
                "user_id": c.user_id,
                "name": user.name,
                "email": user.email,
                "api_key": decrypt_value(c.encrypted_api_key),
                "api_secret": decrypt_value(c.encrypted_api_secret),
                "ibkr_client_id": c.ibkr_client_id
            })
        return results
