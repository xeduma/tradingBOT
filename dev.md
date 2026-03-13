### installation des packets
```bash
sudo apt install -y \
  python3.12 \
  python3.12-venv \
  python3.12-dev \
  python3-pip \
  git \
  curl \
  wget \
  build-essential \
  libpq-dev \
  docker.io \
  docker-compose


# Démarrer le service Docker
sudo systemctl enable docker
sudo systemctl start docker

# Ajouter votre utilisateur au groupe docker (évite sudo)
sudo usermod -aG docker $USER

# Appliquer le groupe sans déconnexion
newgrp docker

```
### python
```bash
# Créer le venv dans le dossier du projet
python3.12 -m venv venv

# Activer l'environnement
source venv/bin/activate

# Vérification : le prompt doit afficher (venv)
which python   # → /home/u

# mise e ajour de pip
pip install --upgrade pip setuptools wheel
```
### python packages
```python
pip install -r requirements.txt
```
### BDD
```bash
# Démarrer seulement la base de données
docker-compose up -d timescaledb

# Attendre qu'elle soit prête (10-20 secondes)
docker-compose logs timescaledb

# Vérifier qu'elle répond
docker exec apex-timescaledb pg_isready -U apex -d trading
# → /var/run/postgresql:5432 - accepting connections

# VÉRIFIER LA CONNEXION DEPUIS PYTHON
python3 test_bdd.py
```

### lancement de l'application
```bash
# mode A 
docker-compose up -d timescaledb

# mode B
# Lancer TOUT en une commande
# (TimescaleDB + Moteur + Prometheus + Grafana)
docker compose up -d

# Suivre les logs du moteur en temps réel
docker compose logs -f trading-engine

# Vérifier que tous les services tournent
docker compose ps

# mode C
# Installer screen si nécessaire
sudo apt install -y screen

# Créer une session persistante
screen -S apex

# Dans la session screen
source venv/bin/activate
python main.py

# Détacher la session (garde le process actif)
Ctrl+A puis D

# Revenir à la session plus tard
screen -r apex

# Lister toutes les sessions
screen -ls
```
### arret propre
```bash
# Docker — arrêt avec fermeture propre des positions
docker compose down

# Local — Ctrl+C dans le terminal (envoie SIGINT)
Ctrl+C
# Le moteur ferme toutes les positions avant de s'arrêter
```
