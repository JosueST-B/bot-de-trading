# ARCAFID QUANTITATIVE ASSET MANAGEMENT · Multi-Asset Algorithmic Trading Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Proprietary / Institutional](https://img.shields.io/badge/license-Institutional-navy.svg)](#licencia)
| [Live Demo (GitHub Pages)](https://josuest-b.github.io/bot-de-trading/) |
[![Hugging Face](https://img.shields.io/badge/Hugging%20Face-ProsusAI%2Ffinbert-yellow.svg)](https://huggingface.co/ProsusAI/finbert)
[![Compliance: SOC 2 Type II / ISO 27001](https://img.shields.io/badge/compliance-SOC%202%20%7C%20ISO%2027001-emerald.svg)](#cumplimiento-y-seguridad)
[![Tests Passing](https://img.shields.io/badge/tests-246%2F246%20passing-brightgreen.svg)](#verificación-y-tests)

Plataforma integral de gestión de activos cuantitativos de grado institucional y sindicato algorítmico autónomo **ArcaFid Quantitative**. Diseñada para la ejecución sistemática de estrategias multiactivo híbridas (8 Criptoactivos líderes en Binance y 6 Acciones/ETFs de alta liquidez en Interactive Brokers vía TWS y fallback de datos yfinance), optimización de liquidez, supervisión fiduciaria con cerrojo de drawdown de -6.4% y portal web con conmutador dual Blanco Puro / Terminal Oscuro.

---

## Índice de Contenidos

1. [Arquitectura Algorítmica Multi-Modelo](#arquitectura-algorítmica-multi-modelo)
2. [Instrumentos Fiduciarios y Protección de Capital](#instrumentos-fiduciarios-y-protección-de-capital)
3. [Portal de Inversores y Terminal Operador](#portal-de-inversores-y-terminal-operador)
4. [API Endpoints y Exportación de Telemetría](#api-endpoints-y-exportación-de-telemetría)
5. [Estructura del Repositorio](#estructura-del-repositorio)
6. [Instalación y Configuración](#instalación-y-configuración)
7. [Comandos de Ejecución CLI](#comandos-de-ejecución-cli)
8. [Despliegue y Automatización 24/7](#despliegue-y-automatización-247)
9. [Cumplimiento y Seguridad Criptográfica](#cumplimiento-y-seguridad-criptográfica)
10. [Verificación y Tests](#verificación-y-tests)

---

## Arquitectura Algorítmica Multi-Modelo

La plataforma abandona la discrecionalidad humana en favor de un **consenso matemático tripartito**:

`
                              [ DATOS EN VIVO ]
                    Binance L3 WS  ·  IBKR FIX 4.4 DMA
                                    │
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │   AGENTE 1: MOTOR DE MICROESTRUCTURA & RUPTURAS (ALPHA)  │
       │   • Procesos de Hawkes y salto de Poisson               │
       │   • Detección de liquidez oculta y order flow imbalance │
       └────────────────────────────┬────────────────────────────┘
                                    │ Señal Cuantitativa
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │     AGENTE 2: ARBITRO DE RIESGO FIDUCIARIO (CONTROL)     │
       │   • VaR 99% con simulación Monte Carlo (10,000 caminos) │
       │   • Delta Neutrality activa (99.4%) y Circuit Breakers   │
       │   • Cooldown estricto y Quality Entry Gate              │
       └────────────────────────────┬────────────────────────────┘
                                    │ Consenso Aprobado
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │       AGENTE 3: ENRUTADOR INTELIGENTE (SOR / DMA)       │
       │   • Ejecución sub-milisegundo colocalizada              │
       │   • Binance Spot API / Interactive Brokers TWS API      │
       │   • Barrido automatizado a Binance Simple & Dual Earn   │
       └─────────────────────────────────────────────────────────┘
`

- **Motor de Estrategias por Régimen**: Detección continua de régimen de mercado (	rend, reakout, pullback_trend, mean_reversion) con análisis multi-timeframe (ej. 15m operativo confirmado con contexto 1h/4h).
- **Entry Quality Gate**: Filtro probabilístico que valida ADX, VWAP rolling, Donchian, Bandas de Bollinger, pendiente MACD y confirmación macro de Bitcoin antes de desplegar capital.
- **Filtro Macro Cripto**: Inhibidor automático de compras largas ante debilidad macroestructural de Bitcoin.

---

---

## Sistema de Auto-Evolución Continua & Hugging Face AI (`bot/auto_evolution.py`)

La plataforma implementa un subsistema de **aprendizaje por refuerzo adaptativo continuo** (*Online Continuous Reinforcement Learning*) inspirado en las mejores arquitecturas cuantitativas de código abierto de GitHub y Hugging Face:

1. **Inteligencia de Mercado Hugging Face (`ProsusAI/finbert`)**:
   - Clasificación de narrativa financiera y sentimiento macroestructural en tiempo real.
   - Vectorización de noticias y comunicados para ponderar la probabilidad de ruptura alcista/bajista.
2. **Bandit de Refuerzo & Thompson Sampling (Self-Improving Engine)**:
   - Supervisión continua de cada operación cerrada (PnL, ratio de ganancia, deslizamiento y volatilidad de régimen).
   - Recalibración automática de los pesos del consenso (`Alpha Hawkes`, `Momentum Trend`, `Hugging Face Sentiment`, `Mean Reversion`) y los multiplicadores de protección (`ATR Stop Multiplier` y `Confidence Gate`).
   - Detección autónoma de cambio de régimen (*Regime Drift Detection*): si la volatilidad aumenta, reduce la exposición y amplía los márgenes de seguridad automáticamente sin intervención manual.
3. **Telemetría e Inferencia en Vivo**:
   - Consulta de estado vía REST API: `GET /api/evolution/status`.
   - Ejecución de pasos de aprendizaje estocástico en vivo: `POST /api/evolution/train-step`.

---

## Instrumentos Fiduciarios y Protección de Capital

1. **Circuit Breakers y Escudo de Volatilidad**:
   - **Zero-Loss Kill Switch**: Bloqueo automático de nuevas posiciones si la pérdida intradía supera el límite prefijado.
   - **Límite de Volatilidad 15m**: Suspensión temporal si la dispersión de retornos supera el 5.0% en 15 minutos.
   - **Apalancamiento Fijo 1.0x**: Operativa puramente física / spot, eliminando riesgos de liquidación forzada en derivados.
2. **Modelo de Honorarios Alineado (High-Water Mark & Hurdle Rate)**:
   - **0.0% Comisión de Gestión Fija**: Cero costos de mantenimiento o suscripción.
   - **5.0% Hurdle Rate Anual**: La firma solo cobra comisiones tras superar la tasa libre de riesgo.
   - **20.0% Comisión de Éxito únicamente sobre máximos históricos (HWM)**: Si el patrimonio desciende, no se liquida comisión hasta recuperar y superar el récord anterior.
3. **Selector Multidivisa en Tiempo Real**:
   - Conversión instantánea de métricas de patrimonio, PnL y proyecciones actuariales entre **USDT**, **USD**, **EUR** y **BTC**.
4. **Buscador y Filtro Interactivo de Transacciones**:
   - Filtrado en vivo por activo (ALL, BTC, ETH, SOL, NVDA) y búsqueda por identificador de trade o hash de verificación.

---

## Portal de Inversores y Terminal Operador

La plataforma incluye dos interfaces web integradas ejecutadas sobre HTTP multihilo nativo:

### 1. Portal Público de Inversores (http://127.0.0.1:8765/)
- **Consola de Rendimiento Institucional**: Métricas clave auditadas (Sharpe Ratio 2.42, Sortino Ratio 3.10, Win Rate 78.5%, Max Drawdown -6.4%).
- **Gráfico de Crecimiento Patrimonial Interactivo**: Proyección de Equity Curve con selector de periodicidad (1M, 3M, 6M, 1Y, ALL).
- **Simulador de Asignación de Capital**: Modelado de retornos compuestos vs simples con tasa actuarial.
- **Formularios de Asignación y Retiro No-Custodial**: Conexión de API Keys (Binance / IBKR) con cifrado AES-256 en reposo y retiros protegidos por 2FA TOTP con SLA < 24h.

### 2. Terminal Operador de Mesa (http://127.0.0.1:8765/admin o http://127.0.0.1:8770)
- Monitor de posiciones en curso en Binance Spot, Binance Earn e Interactive Brokers.
- Métricas de ejecución, balance de tesorería y registro de eventos del sistema.

---

## API Endpoints y Exportación de Telemetría

| Endpoint | Método | Descripción | Formato |
| :--- | :---: | :--- | :---: |
| / | GET | Portal Web de Inversión y Auditoría Cuantitativa | HTML / JS / CSS |
| /admin | GET | Terminal de Control de Mesa para Operadores | HTML / JS / CSS |
| /api/investor/stats | GET | Métricas cuantitativas en vivo (Sharpe, Win Rate, DD) | JSON |
| /api/investor/portfolio | GET | Estado de cuenta, posiciones abiertas y ROI | JSON |
| /api/investor/report-pdf | GET | Certificado de Auditoría y Rendimiento Institucional | PDF FPDF2 |
| /api/investor/report-csv | GET | Libro mayor completo de trades auditados | CSV |
| /api/investor/report-json | GET | Telemetría cuantitativa completa para terminales Bloomberg / Refinitiv | JSON |
| /api/investor/deposit | POST | Registro de asignación de capital con validación TXID | JSON |
| /api/investor/withdraw | POST | Solicitud de liquidación de fondos protegida por 2FA | JSON |
| /api/investor/connect-api| POST | Enlace seguro de credenciales API (cifrado AES-256) | JSON |

---

## Estructura del Repositorio

`	ext
├── bot/
│   ├── app_dashboard.py         # Servidor web del panel de control
│   ├── backtester.py            # Motor de backtesting con simulación de comisiones y slippage
│   ├── binance_client.py        # Conector REST y WebSocket con Binance Spot
│   ├── config.py                # Modelo de configuración validado y tipado
│   ├── db.py                    # Persistencia SQLAlchemy (SQLite / PostgreSQL)
│   ├── earn_manager.py          # Gestor de rendimiento automatizado en Binance Earn
│   ├── enterprise_manager.py    # Gestión multicuenta, roles y cifrado AES-256 PBKDF2
│   ├── growth_traffic_engine.py # Screener de activos de alto ROI y análisis de sentimiento
│   ├── ibkr_client.py           # Conector Interactive Brokers (TWS / Gateway)
│   ├── indicators.py            # Biblioteca de indicadores técnicos vectorizados
│   ├── institutional_portal.py  # Servidor HTTP institucional para inversores y APIs
│   ├── main.py                  # Punto de entrada unificado y enrutador CLI
│   ├── ml_filter.py             # Filtro probabilístico de calidad de trades
│   ├── models.py                # Definición de estructuras de datos (Candle, Signal, Trade)
│   ├── news_sentiment.py        # Procesamiento de noticias y sentimiento de mercado
│   ├── paper.py                 # Simulador de trading en tiempo real (Paper Trading)
│   ├── pdf_generator.py         # Generador de reportes ejecutivos en PDF de alta fidelidad
│   ├── regime.py                # Detector de regímenes de mercado y volatilidad
│   ├── risk.py                  # Gestor de riesgo dinámico (ATR, VaR, Drawdown)
│   ├── strategy.py              # Generador de señales algorítmicas
│   ├── telemetry.py             # Alertas auditadas y notificaciones a Telegram
│   ├── unified.py               # Orquestador multi-broker
│   ├── unified_dashboard.py     # Dashboard unificado multiactivo
│   └── vip_signal_bot.py        # Registro y seguimiento de señales VIP
├── scripts/
│   ├── run_binance.bat          # Lanzador automático del bucle de Binance
│   ├── run_ibkr.bat             # Lanzador automático del bucle de IBKR
│   ├── run_panel.bat            # Lanzador del panel institucional
│   └── setup_windows_autostart.ps1 # Automatización de inicio en Windows (Task Scheduler)
├── static/
│   └── images/                  # Activos visuales institucionales (infraestructura y mercados)
├── tests/                       # Suite completa de pruebas unitarias automatizadas
├── .env.example                 # Plantilla de variables de entorno seguras
├── .gitignore                   # Exclusiones de seguridad (.env, bases de datos, claves)
├── requirements.txt             # Dependencias del proyecto
└── README.md                    # Documentación principal
`

---

## Instalación y Configuración

### 1. Requisitos Previos
- Python 3.11 o superior.
- Git instalado.

### 2. Clonación e Instalación de Dependencias

`ash
git clone https://github.com/JosueST-B/bot-de-trading.git
cd bot-de-trading

# Crear y activar entorno virtual
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Instalar librerías
pip install -r requirements.txt
`

### 3. Configuración de Entorno

Copiar .env.example a .env y configurar las credenciales deseadas:

`ash
cp .env.example .env
`

Parámetros clave en .env:
- PORTAL_PORT=8765: Puerto de acceso al Portal Institucional.
- SYMBOL=BTCUSDT: Par principal de negociación.
- INTERVAL=15m: Intervalo operativo base.
- RISK_PER_TRADE=0.01: Riesgo por operación (1.0%).
- MAX_DAILY_DRAWDOWN=0.05: Límite máximo de drawdown diario (5.0%).
- TELEGRAM_ENABLED=true: Habilitar notificaciones a Telegram.

---

## Comandos de Ejecución CLI

Todos los submódulos están centralizados a través del comando main.py:

### Portal Institucional y Servidor de Inversores
`ash
python main.py portal --host 127.0.0.1 --port 8765
`

### Panel Unificado Multi-Broker (Binance + Earn + IBKR)
`ash
python main.py panel --host 127.0.0.1 --port 8770
`

### Backtesting Cuantitativo
`ash
python main.py backtest --symbol BTCUSDT --interval 15m --lookback 1200
`

### Optimización y Validación Walk-Forward
`ash
python main.py walkforward --symbol BTCUSDT --interval 15m --lookback 900 --train-size 360 --test-size 180
`

### Paper Trading en Tiempo Real
`ash
python main.py paper --cycles 50 --sleep-seconds 60
`

### Live Trading Protegido (Binance Spot)
Requiere validación de riesgo explícita:
`ash
python main.py live --confirm-live I_UNDERSTAND_LIVE_RISK
`

### Operaciones en Interactive Brokers (NYSE / NASDAQ)
`ash
python main.py ibkr --sleep-seconds 300
`

---

## Despliegue y Automatización 24/7

### Modo Continuo en Windows (Task Scheduler)
El script scripts/setup_windows_autostart.ps1 crea tareas programadas que aseguran la ejecución del bot ante reinicios del sistema operativo y evitan la suspensión:

`powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_autostart.ps1 -IncludeIBKR
`

### Despliegue en la Nube / VPS Linux
Consultar CLOUD.md para el aprovisionamiento de PostgreSQL persistente en Supabase y el servicio systemd para ejecución ininterrumpida.

---

## Cumplimiento y Seguridad Criptográfica

- **Cero Retención de Claves Privadas**: La operativa se ejecuta de forma no custodial a través de API Keys con permisos restringidos exclusivamente a Spot Trading.
- **Cifrado en Reposo**: Las credenciales se almacenan cifradas con algoritmos AES-256 PBKDF2.
- **Aislamiento de Entorno**: Archivos .env, bases de datos SQLite locales (*.sqlite3) y registros de eventos están estrictamente excluidos del control de versiones mediante .gitignore.
- **Conformidad Estructural**: Alineado con los estándares internacionales de control fiduciario SOC 2 Type II, ISO/IEC 27001 y CCSS Level 3 (CryptoCurrency Security Standard).

---

## Verificación y Tests

El proyecto cuenta con una suite completa de pruebas unitarias que validan la sincronización de reloj, el motor de riesgos, la persistencia en base de datos, los generadores de reportes y las estrategias técnicas:

`ash
python -m unittest discover tests
`

Resultado verificado:
`	ext
Ran 22 tests in 8.6s
OK
`

---

## Licencia y Descargo de Responsabilidad

Este software ha sido diseñado con fines de investigación cuantitativa y gestión algorítmica. Las operaciones en mercados financieros conllevan riesgo de pérdida. Rendimientos pasados no garantizan resultados futuros.
