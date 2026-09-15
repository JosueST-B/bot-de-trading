import logging
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

class MLFilter:
    def __init__(self):
        self.model = None
        self.is_trained = False

    def _extract_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
        """Extrae características normalizadas a partir del DataFrame de klines."""
        if len(df) < 50:
            return pd.DataFrame(), None

        data = df.copy()
        
        # 1. Ratios de Medias Móviles
        data["sma_10"] = data["close"].rolling(10).mean()
        data["sma_30"] = data["close"].rolling(30).mean()
        data["sma_ratio"] = data["sma_10"] / (data["sma_30"] + 1e-9)
        
        # 2. RSI simple
        delta = data["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        data["rsi"] = 100 - (100 / (1 + rs))
        
        # 3. Ancho de Bandas de Bollinger y ATR relativo
        data["bb_mid"] = data["close"].rolling(20).mean()
        data["bb_std"] = data["close"].rolling(20).std()
        data["bb_width"] = (data["bb_std"] * 4.0) / (data["bb_mid"] + 1e-9)
        
        high_low = data["high"] - data["low"]
        high_close = (data["high"] - data["close"].shift()).abs()
        low_close = (data["low"] - data["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        data["atr"] = tr.rolling(14).mean()
        data["atr_pct"] = data["atr"] / (data["close"] + 1e-9)
        
        # 4. Retornos pasados
        data["return_1"] = data["close"].pct_change(1)
        data["return_3"] = data["close"].pct_change(3)
        
        feature_cols = ["sma_ratio", "rsi", "bb_width", "atr_pct", "return_1", "return_3"]
        features = data[feature_cols].copy()
        
        features = features.ffill().bfill()
        
        labels = None
        if "close" in data.columns:
            future_return = data["close"].shift(-3) / (data["close"] + 1e-9) - 1.0
            labels = (future_return > 0.001).astype(int)
            
        return features, labels

    def train(self, df: pd.DataFrame) -> None:
        """Entrena el clasificador RandomForest en base a velas históricas."""
        if len(df) < 100:
            logging.warning("No hay suficientes velas históricas para entrenar el modelo de ML.")
            return

        try:
            features, labels = self._extract_features(df)
            if features.empty or labels is None:
                return

            X = features.iloc[:-3]
            y = labels.iloc[:-3]

            if len(X) < 50:
                return

            self.model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
            self.model.fit(X, y)
            self.is_trained = True
            logging.info(f"Modelo predictivo de Machine Learning entrenado exitosamente con {len(X)} muestras.")
        except Exception as e:
            logging.error(f"Error al entrenar el modelo predictivo de ML: {e}")

    def predict_probability(self, df: pd.DataFrame) -> float:
        """Devuelve la probabilidad (0.0 a 1.0) de que la señal sea ganadora."""
        if not self.is_trained or self.model is None:
            return 1.0

        try:
            features, _ = self._extract_features(df)
            if features.empty:
                return 1.0
                
            latest_feature = features.iloc[[-1]]
            latest_feature = latest_feature.replace([np.inf, -np.inf], np.nan).fillna(0.0)
            
            proba = self.model.predict_proba(latest_feature)[0][1]
            return float(proba)
        except Exception as e:
            logging.error(f"Error en predicción probabilística de ML: {e}")
            return 1.0
