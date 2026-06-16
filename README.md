# SNDE Recouvrement - Backend 

Backend Django/DRF du systeme SNDE Recouvrement.

Ce depot est la livraison backend uniquement. Il contient le code serveur, les migrations, Docker Compose, Celery, la synchronisation AWS S3 et le modele IA.

Repo :

```text
https://github.com/Medyahye/backend-snde-recouvrement.git
```

## Contenu

- API Django REST + JWT
- PostgreSQL
- Redis
- Celery worker et Celery Beat
- MinIO pour FAB/photos terrain
- import et traitement des FAB
- synchronisation AWS S3 vers MinIO
- scoring metier
- scoring IA FT-Transformer
- affectations mobile terrain
- retours terrain avec photo + GPS

## A donner separement

Ces elements sont fournis separement :

- vrai `.env`
- dump PostgreSQL complet : `snde_full_backup.dump`
- secrets AWS
- credentials serveur
- fichiers Apple/TestFlight
- frontend web
- application mobile

Ne jamais commiter `.env`, dumps, cles ou secrets.

## Architecture Docker

Services :

- `backend` : Django + Gunicorn sur port `8000`
- `postgres` : PostgreSQL `pgvector/pgvector:pg16`
- `redis` : broker/result backend Celery
- `minio` : stockage objet local des FAB/photos
- `minio-init` : creation des buckets MinIO
- `celery_worker` : execution imports/scoring/sync
- `celery_beat` : planification de la sync S3 quotidienne

Ports exposes par defaut :

```text
8000  backend API
5432  PostgreSQL
6379  Redis
9000  MinIO API
9001  MinIO console
```

## Installation Serveur

Prerequis :

- Docker
- Docker Compose v2
- acces au repo GitHub
- fichier `.env` reel fourni hors Git

Clone :

```bash
git clone https://github.com/Medyahye/backend-snde-recouvrement.git
cd backend-snde-recouvrement
```

Creer `.env` :

```bash
cp .env.example .env
```

Modifier `.env` avec les vraies valeurs.

Demarrer :

```bash
docker compose up -d --build
```

Verifier :

```bash
docker compose ps
docker logs snde-backend --tail 100
```

## Variables .env A Renseigner

### Django

```env
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=api-domain.example.com,localhost,127.0.0.1,backend
DJANGO_SUPERUSER_EMAIL=admin@snde.local
DJANGO_SUPERUSER_PASSWORD=...
```

En production, mettre `DJANGO_DEBUG=0`.

### PostgreSQL

```env
POSTGRES_DB=snde
POSTGRES_USER=snde
POSTGRES_PASSWORD=...
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
```

Si la base est RDS/AWS externe, remplacer `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, etc.

### Redis / Celery

```env
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
```

Si Redis est externe, remplacer l'host.

### MinIO / Stockage Objet

```env
MINIO_ROOT_USER=...
MINIO_ROOT_PASSWORD=...
MINIO_ENDPOINT=minio:9000
MINIO_PUBLIC_ENDPOINT=https://minio-or-public-host.example.com
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET_FAB=fab-imports
MINIO_BUCKET_PHOTOS=meter-photos
MINIO_USE_SSL=0
```

`MINIO_PUBLIC_ENDPOINT` doit etre accessible par le mobile si les photos terrain doivent s'afficher.

### AWS S3 Source FAB

```env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=snde-facture
AWS_S3_REGION=eu-west-1
AWS_S3_PREFIX=Solde/
FAB_EMPTY_MIN_VALID_LINES=100
```

Ces credentials doivent etre read-only sur S3.

### Scoring IA

```env
SCORING_ENGINE=ft_transformer
```

Pour utiliser le score metier classique :

```env
SCORING_ENGINE=formula
```

## Modele IA

Le modele IA est inclus dans le repo :

```text
backend/gnn_models/ft_transformer_snde.pt
```

Le code d'inference est ici :

```text
backend/apps/scoring/ft_transformer.py
```

Le modele utilise les features client/FAB et les champs historiques si disponibles. Les probabilites sont exposees dans :

```text
clients.proba_paiement
clients.score_final
```

## Restauration De La Base Complete

Le dump complet est fourni hors Git :

```text
snde_full_backup.dump
```

Copier le dump sur le serveur, dans le dossier du repo ou ailleurs.

Si PostgreSQL tourne dans Docker Compose :

```bash
docker cp snde_full_backup.dump snde-postgres:/tmp/snde_full_backup.dump
docker exec snde-postgres pg_restore -U snde -d snde --clean --if-exists /tmp/snde_full_backup.dump
```

Si la base cible est vide mais existe deja, cette commande restaure les tables/donnees.

Verifier apres restauration :

```bash
docker exec snde-postgres psql -U snde -d snde -c "select count(*) from clients;"
docker exec snde-postgres psql -U snde -d snde -c "select count(*) from fab_imports;"
docker exec snde-postgres psql -U snde -d snde -c "select count(*) from terrain_assignments;"
```

Nettoyer le dump temporaire dans le conteneur :

```bash
docker exec snde-postgres rm -f /tmp/snde_full_backup.dump
```

## Donnees Initiales

Pour cette livraison, la base doit etre initialisee avec le dump complet fourni :

```text
snde_full_backup.dump
```

Ce dump contient les donnees deja preparees pour la demonstration et l'exploitation :

- imports FAB deja traites
- clients
- scores
- probabilites IA
- releveurs/utilisateurs
- affectations terrain

La reconstruction historique depuis S3 n'est pas la procedure de livraison recommandee, car elle peut prendre beaucoup de temps. S3 reste utilise ensuite pour les nouveaux FAB via la synchronisation automatique quotidienne.

## Migrations

Au demarrage, le service `backend` execute :

```bash
python manage.py migrate --noinput
python manage.py create_default_admin
```

Execution manuelle si necessaire :

```bash
docker exec snde-backend python manage.py migrate
docker exec snde-backend python manage.py check
```

## Synchronisation S3 Automatique

Celery Beat lance la tache :

```text
scoring.sync_s3_daily
```

Planification :

```text
tous les jours a 02:00 Africa/Nouakchott
```

Logs :

```bash
docker logs snde-celery-beat --tail 200
docker logs snde-celery-worker --tail 200
```

## Commandes Utiles

Statut :

```bash
docker compose ps
```

Logs backend :

```bash
docker logs snde-backend --tail 200
```

Logs worker :

```bash
docker logs snde-celery-worker --tail 200
```

Shell Django :

```bash
docker exec -it snde-backend python manage.py shell
```

Verifier l'API :

```bash
curl http://localhost:8000/api/
```

Verifier la base :

```bash
docker exec snde-postgres psql -U snde -d snde -c "select pg_size_pretty(pg_database_size('snde'));"
```



Sans URL publique accessible depuis Internet, l'app TestFlight ne pourra pas fonctionner depuis la maison du directeur.

## Checklist DevOps

1. Cloner le repo.
2. Creer `.env` depuis `.env.example`.
3. Renseigner secrets Django/Postgres/Redis/MinIO/AWS.
4. Lancer `docker compose up -d --build`.
5. Restaurer `snde_full_backup.dump`.
6. Verifier `docker compose ps`.
7. Verifier `python manage.py check`.
8. Verifier les counts PostgreSQL (`clients`, `fab_imports`, `terrain_assignments`).



- Utiliser des credentials AWS read-only pour la source FAB.
- Sauvegarder PostgreSQL en production.
