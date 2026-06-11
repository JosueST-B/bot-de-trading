import pandas as pd
import pandas_ta as ta

class BaseStrategy:
    """
    Clase base para todas las estrategias de trading.
    Define las interfaces y métodos comunes para calcular indicadores y generar señales.
    """
    def __init__(self, name="Base Strategy"):
        self.name = name

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Debe ser implementado por la estrategia específica.
        Toma un DataFrame con datos OHLCV y devuelve el mismo DataFrame
        con una nueva columna 'signal' (1=Compra, -1=Venta, 0=Mantener).
        """
        raise NotImplementedError("Cada estrategia debe implementar 'generate_signals'")

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Debe ser implementado por la estrategia específica.
        Calcula y añade los indicadores técnicos necesarios al DataFrame.
        """
        raise NotImplementedError("Cada estrategia debe implementar 'add_indicators'")
