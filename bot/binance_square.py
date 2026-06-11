from __future__ import annotations

import logging
import requests
from bot.config import BotConfig


class BinanceSquarePublisher:
    API_URL = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"

    def __init__(self, cfg: BotConfig) -> None:
        self.enabled = cfg.binance_square_enabled and bool(cfg.binance_square_api_key)
        self.api_key = cfg.binance_square_api_key

    def publish_post(self, text: str) -> bool:
        """Envía una publicación de texto plano a Binance Square utilizando OpenAPI."""
        if not self.enabled:
            return False

        headers = {
            "X-Square-OpenAPI-Key": self.api_key,
            "Content-Type": "application/json",
            "clienttype": "binanceSkill"
        }
        payload = {
            "bodyTextOnly": text
        }

        try:
            logging.info("Enviando publicación a Binance Square...")
            response = requests.post(
                self.API_URL,
                json=payload,
                headers=headers,
                timeout=15
            )
            response.raise_for_status()
            
            res_data = response.json()
            # El bapi de Binance suele retornar success=True o code="000000" en caso de éxito.
            if res_data.get("code") == "000000" or res_data.get("success") is True or res_data.get("status") == "success":
                logging.info("Publicación en Binance Square enviada con éxito.")
                return True
            else:
                logging.error(f"Error en respuesta de Binance Square: {res_data}")
                return False
        except Exception as e:
            logging.error(f"Fallo al publicar en Binance Square: {e}")
            return False
