FROM python:3.12-slim

# Dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installation des dépendances Python (couche cache optimisée)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code source
COPY . .

# Init files pour les packages Python
RUN touch core/__init__.py strategies/__init__.py data/__init__.py \
         monitoring/__init__.py utils/__init__.py

# Utilisateur non-root pour la sécurité
RUN useradd -m -u 1000 apex && chown -R apex:apex /app
USER apex

# Healthcheck (vérifie que Prometheus répond)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/metrics || exit 1

# Démarrage du moteur
CMD ["python", "-u", "main.py"]
