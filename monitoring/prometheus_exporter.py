"""
Exporteur Prometheus — metriques temps reel pour Grafana.
"""

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from utils.logger import setup_logger

logger = setup_logger("prometheus")


class PrometheusExporter:

    def __init__(self, port=8000):
        self.port = port

        self.pnl_usd = Gauge("apex_pnl_usd", "PnL journalier en USD")
        self.open_positions = Gauge("apex_open_positions", "Nombre de positions ouvertes")
        self.capital = Gauge("apex_capital_usd", "Capital disponible en USD")
        self.cycle_duration = Gauge("apex_cycle_duration_s", "Duree du dernier cycle en secondes")
        self.price_gauge = Gauge("apex_price", "Dernier prix d'un actif", ["symbol"])
        self.mistral_score_gauge = Gauge(
            "apex_mistral_score",
            "Dernier score Mistral par actif",
            ["symbol"]
        )
        self.mistral_threshold_gauge = Gauge(
            "apex_mistral_threshold",
            "Seuil minimum requis pour ouvrir une position"
        )
        self.mistral_enabled_gauge = Gauge(
            "apex_mistral_enabled",
            "1 si Mistral est active, 0 sinon"
        )

        self.orders_total = Counter(
            "apex_orders_total", "Total ordres executes", ["side"]
        )
        self.errors_total = Counter(
            "apex_errors_total", "Total erreurs", ["type"]
        )
        self.order_latency = Histogram(
            "apex_order_latency_ms",
            "Latence des ordres en millisecondes",
            buckets=[5, 10, 20, 30, 50, 75, 100, 200, 500]
        )

    async def start(self):
        start_http_server(self.port)
        logger.info(f"Prometheus metrics exposees sur http://0.0.0.0:{self.port}/metrics")

    def update_pnl(self, pnl):
        self.pnl_usd.set(pnl)

    def set_open_positions(self, n):
        self.open_positions.set(n)

    def set_capital(self, capital):
        self.capital.set(capital)

    def set_cycle_duration(self, duration_s):
        self.cycle_duration.set(duration_s)

    def update_price(self, symbol, price):
        clean = symbol.replace("/", "_").replace("-", "_")
        self.price_gauge.labels(symbol=clean).set(price)

    def update_mistral_score(self, symbol, score):
        clean = symbol.replace("/", "_").replace("-", "_")
        self.mistral_score_gauge.labels(symbol=clean).set(score)

    def set_mistral_threshold(self, threshold):
        self.mistral_threshold_gauge.set(threshold)

    def set_mistral_enabled(self, enabled):
        self.mistral_enabled_gauge.set(1 if enabled else 0)

    def record_order_latency(self, ms):
        self.order_latency.observe(ms)

    def increment_trades(self, side):
        self.orders_total.labels(side=side).inc()

    def increment_error(self, error_type="general"):
        self.errors_total.labels(type=error_type).inc()
