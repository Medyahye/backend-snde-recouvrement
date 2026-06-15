# SNDE Recouvrement - Backend

Backend Django/DRF du systeme de recouvrement SNDE.

Le depot contient uniquement la partie backend livrable :

- API Django REST + JWT
- import et parsing des FAB
- scoring metier et scoring IA FT-Transformer
- synchronisation AWS S3 vers MinIO
- traitements asynchrones Celery
- affectations terrain mobile
- preuves de passage terrain avec photo/GPS

## Architecture

Services Docker :

- `backend` : API Django exposee sur `:8000`
- `postgres` : base PostgreSQL avec image `pgvector/pgvector:pg16`
- `redis` : broker/result backend Celery
- `minio` : stockage local des FAB et photos compteur
- `celery_worker` : execution des imports, scoring, sync S3
- `celery_beat` : planification quotidienne de la sync S3

## Prerequis

- Docker
- Docker Compose v2
- Acces aux secrets `.env` fournis hors Git
- Credentials AWS S3 read-only si la synchronisation automatique est active

## Installation locale

```bash
cp .env.example .env
```

Modifier ensuite `.env` avec les vraies valeurs.

Demarrer :

```bash
docker compose up -d --build
```

Verifier :

```bash
docker compose ps
docker logs snde-backend --tail 100
```

Appliquer les migrations manuellement si besoin :

```bash
docker exec snde-backend python manage.py migrate
```

Creer le compte admin par defaut :

```bash
docker exec snde-backend python manage.py create_default_admin
```

## Variables d'environnement

Les variables attendues sont documentees dans `.env.example`.

Principales sections :

- Django : `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`
- PostgreSQL : `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- Celery/Redis : `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- MinIO : `MINIO_ENDPOINT`, `MINIO_BUCKET_FAB`, `MINIO_BUCKET_PHOTOS`
- AWS S3 : `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`
- Scoring : `SCORING_ENGINE=formula` ou `SCORING_ENGINE=ft_transformer`

Ne jamais commiter le vrai `.env`.

## IA / Scoring

Le modele IA livre est :

```text
backend/gnn_models/ft_transformer_snde.pt
```

Pour utiliser le score IA :

```env
SCORING_ENGINE=ft_transformer
```

Pour revenir au score metier manuel :

```env
SCORING_ENGINE=formula
```

Le backend expose les champs `proba_paiement` et `score_final` aux interfaces web/mobile.

## Sync AWS S3

Le systeme lit les FAB depuis le bucket AWS S3 configure, puis les copie dans MinIO avant traitement.

La tache planifiee tourne tous les jours a 02:00 heure `Africa/Nouakchott` via Celery Beat.

Declenchement manuel possible depuis l'API/admin web si active dans l'interface.

Commandes utiles :

```bash
docker logs snde-celery-worker --tail 200
docker logs snde-celery-beat --tail 200
```

## Mobile terrain

Le backend gere les routes terrain suivantes :

- affectations du releveur/agent terrain
- clients en code relance `1`
- saisie retour terrain
- photo compteur stockee dans MinIO
- latitude/longitude de preuve de passage
- statut de visite : a faire, fait, absent, inaccessible, anomalie

## Commandes utiles

Verifier Django :

```bash
docker exec snde-backend python manage.py check
```

Lister les migrations :

```bash
docker exec snde-backend python manage.py showmigrations
```

Ouvrir un shell Django :

```bash
docker exec -it snde-backend python manage.py shell
```

## Livraison

Ce depot ne contient pas :

- le vrai `.env`
- les secrets AWS
- les fichiers Apple `.p8`
- les datasets d'entrainement
- les logs locaux
- le frontend web
- l'application mobile

Ces elements doivent etre livres ou configures separement selon la politique DevOps SNDE.
