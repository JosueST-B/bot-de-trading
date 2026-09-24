from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import requests

BULLISH_KEYWORDS = [
    r'\bbullish\b', r'\bsurge\b', r'\bpump\b', r'\bgain\b', r'\brally\b',
    r'\bupward\b', r'\bbreakout\b', r'\brose\b', r'\bprofit\b', r'\bgrowth\b',
    r'\badoption\b', r'\bsupport\b', r'\bpartner\b', r'\bpartnership\b',
    r'\bbuy\b', r'\bbuying\b', r'\bupgrade\b', r'\bgreater\b', r'\bhigh\b',
    r'\bhighest\b', r'\bsoar\b', r'\bsoared\b', r'\bsoaring\b', r'\bgreen\b',
    r'\bpositive\b', r'\boptimism\b', r'\boptimistic\b', r'\baccumulation\b'
]

BEARISH_KEYWORDS = [
    r'\bbearish\b', r'\bdrop\b', r'\bdump\b', r'\bloss\b', r'\bcrash\b',
    r'\bdownward\b', r'\bfall\b', r'\bfallen\b', r'\bfalling\b', r'\bdown\b',
    r'\bhack\b', r'\bhacked\b', r'\bexploit\b', r'\bexploited\b', r'\bscam\b',
    r'\bregulation\b', r'\bregulate\b', r'\blawsuit\b', r'\bsec\b', r'\bfine\b',
    r'\bfined\b', r'\bban\b', r'\bbanned\b', r'\bbanning\b', r'\bwarning\b',
    r'\bwarned\b', r'\bpanic\b', r'\bfear\b', r'\bdip\b', r'\bdipped\b',
    r'\bdecline\b', r'\bdeclined\b', r'\bred\b', r'\bnegative\b', r'\bshort\b'
]

BULLISH_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BULLISH_KEYWORDS]
BEARISH_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BEARISH_KEYWORDS]


from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_vader_analyzer = SentimentIntensityAnalyzer()

def analyze_text(text: str) -> float:
    """Calcula el sentimiento de un texto de -1.0 a 1.0 usando VADER."""
    try:
        vs = _vader_analyzer.polarity_scores(text)
        return float(vs["compound"])
    except Exception:
        bull_count = sum(1 for p in BULLISH_PATTERNS if p.search(text))
        bear_count = sum(1 for p in BEARISH_PATTERNS if p.search(text))
        total = bull_count + bear_count
        if total == 0:
            return 0.0
        return (bull_count - bear_count) / total


def analyze_feed(items: list[dict[str, str]]) -> float:
    """Calcula el promedio de sentimiento de una lista de noticias."""
    if not items:
        return 0.0
    scores = []
    for item in items:
        text = f"{item['title']} {item['description']}"
        scores.append(analyze_text(text))
    return sum(scores) / len(scores)


def fetch_latest_news(url: str = "https://cointelegraph.com/rss") -> list[dict[str, str]]:
    """Descarga e interpreta el feed RSS de noticias."""
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall(".//item")[:10]:
            title = item.find("title")
            description = item.find("description")
            pub_date = item.find("pubDate")
            
            title_text = title.text if title is not None else ""
            desc_text = description.text if description is not None else ""
            
            # Limpiar HTML básico
            desc_clean = re.sub(r'<[^<]+?>', '', desc_text).strip()
            
            items.append({
                "title": title_text,
                "description": desc_clean,
                "pub_date": pub_date.text if pub_date is not None else ""
            })
        return items
    except Exception as e:
        import logging
        logging.error(f"Error al descargar noticias RSS de Cointelegraph: {e}")
        return []


class NewsSentimentAnalyzer:
    def __init__(self, db_path: str, url: str = "https://cointelegraph.com/rss"):
        self.db_path = db_path
        self.url = url
        self._cached_score = 0.0
        self._cached_headlines = []
        self._last_fetch_time = None
        
    def get_sentiment(self, force: bool = False, allow_network: bool = True) -> tuple[float, list[dict[str, str]]]:
        """Obtiene el sentimiento de noticias, usando caché local si es posible."""
        now = datetime.utcnow()
        if not force and self._last_fetch_time and (now - self._last_fetch_time).total_seconds() < 3600:
            return self._cached_score, self._cached_headlines
            
        if not force:
            try:
                from bot.db import get_db_session, DBBotState
                session = get_db_session(self.db_path)
                state_record = session.query(DBBotState).filter(DBBotState.key == "news_sentiment").first()
                session.close()
                if state_record:
                    payload = json.loads(state_record.value_json)
                    # El campo updated_ts de SQLAlchemy no tiene timezone en SQLite por defecto
                    updated_ts = state_record.updated_ts
                    if (now - updated_ts).total_seconds() < 3600:
                        self._cached_score = float(payload.get("score", 0.0))
                        self._cached_headlines = payload.get("headlines", [])
                        self._last_fetch_time = updated_ts
                        return self._cached_score, self._cached_headlines
                    elif not allow_network:
                        # Si expiró pero no se permite red, usar caché existente para no bloquear
                        self._cached_score = float(payload.get("score", 0.0))
                        self._cached_headlines = payload.get("headlines", [])
                        return self._cached_score, self._cached_headlines
            except Exception:
                pass

        if not allow_network:
            return self._cached_score, self._cached_headlines

        # Si no hay caché o expiró, descargar de la red
        items = fetch_latest_news(self.url)
        if not items:
            return self._cached_score, self._cached_headlines
            
        score = analyze_feed(items)
        
        self._cached_score = score
        self._cached_headlines = items
        self._last_fetch_time = now
        
        # Guardar en base de datos local
        try:
            from bot.db import get_db_session, DBBotState
            session = get_db_session(self.db_path)
            state_record = session.query(DBBotState).filter(DBBotState.key == "news_sentiment").first()
            payload = {
                "score": score,
                "headlines": items
            }
            if not state_record:
                state_record = DBBotState(
                    key="news_sentiment",
                    value_json=json.dumps(payload)
                )
                session.add(state_record)
            else:
                state_record.value_json = json.dumps(payload)
                state_record.updated_ts = now
            session.commit()
            session.close()
        except Exception:
            pass
            
        return score, items
