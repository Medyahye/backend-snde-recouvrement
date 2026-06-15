"""FT-Transformer pour le scoring des clients SNDE.

Prédit la probabilité de paiement d'un client en coupure (code_relance='1')
en combinant :
  - Les 4 composantes normalisées déjà calculées par la formule (montant, ancienneté,
    historique, arriérés) + le type client + les jours impayés
  - L'historique comportemental du client (ClientBehavior : fréquence paiement,
    score comportemental, récidive coupures)

Architecture : Feature Tokenizer + Transformer (Gorishniy et al. 2021)
  Chaque feature → embedding d_token → Transformer avec [CLS] → probabilité 0-1

Usage :
  # Entraîner (une fois après avoir la base historique) :
  python manage.py train_ft_transformer

  # Activer dans .env :
  SCORING_ENGINE=ft_transformer
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from django.conf import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Hyperparamètres
# ─────────────────────────────────────────────────────────────────────────────
N_FEATURES   = 10
D_TOKEN      = 32
N_HEADS      = 4
N_LAYERS     = 2
DROPOUT      = 0.1
BATCH_SIZE   = 4096
N_EPOCHS     = 40
LR           = 1e-3
PATIENCE     = 6      # early stopping
LABEL_WINDOW = 7      # nb d'imports suivants pour définir "a payé"

# Noms des features dans l'ordre (documentation)
FEATURE_NAMES = [
    "montant_norm",        # 0 : solde normalisé [0,1]
    "anciennete_norm",     # 1 : jours depuis facture / threshold [0,1]
    "historique_norm",     # 2 : jours sans paiement / threshold [0,1]
    "arrieres_norm",       # 3 : arriérés / solde [0,1]
    "coef_type",           # 4 : 0=Domestique, 1=Entreprise
    "jours_impaye_norm",   # 5 : jours impayés / 365 [0,1]
    "behavior_score",      # 6 : score comportemental / 100 [0,1]
    "payment_freq",        # 7 : fréquence paiements historique [0,1]
    "code1_rate",          # 8 : taux coupures historique [0,1]
    "nb_payments_norm",    # 9 : nb paiements / nb apparitions [0,1]
]


def _model_path() -> Path:
    base = Path(settings.BASE_DIR)
    d = base / "gnn_models"
    d.mkdir(exist_ok=True)
    return d / "ft_transformer_snde.pt"


# ─────────────────────────────────────────────────────────────────────────────
# Architecture FT-Transformer
# ─────────────────────────────────────────────────────────────────────────────
class _FTTransformer(nn.Module):
    """Feature Tokenizer + Transformer pour scoring tabular SNDE."""

    def __init__(
        self,
        n_features: int = N_FEATURES,
        d_token: int = D_TOKEN,
        n_heads: int = N_HEADS,
        n_layers: int = N_LAYERS,
        dropout: float = DROPOUT,
    ):
        super().__init__()
        # Tokenizer séparé par feature (feature-specific linear)
        self.tokenizers = nn.ModuleList([
            nn.Linear(1, d_token) for _ in range(n_features)
        ])
        # Token [CLS] appris
        self.cls_token = nn.Parameter(torch.zeros(1, 1, d_token))
        nn.init.trunc_normal_(self.cls_token, std=0.02)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_token,
            nhead=n_heads,
            dim_feedforward=d_token * 4,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        self.head = nn.Sequential(
            nn.LayerNorm(d_token),
            nn.Linear(d_token, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x : [B, N_FEATURES]
        tokens = torch.stack(
            [self.tokenizers[i](x[:, i:i+1]) for i in range(x.size(1))],
            dim=1,
        )  # [B, N_FEATURES, D_TOKEN]

        cls = self.cls_token.expand(x.size(0), -1, -1)  # [B, 1, D_TOKEN]
        tokens = torch.cat([cls, tokens], dim=1)          # [B, 1+N, D_TOKEN]
        tokens = self.transformer(tokens)                  # [B, 1+N, D_TOKEN]

        # Logit sur [CLS]
        return self.head(tokens[:, 0, :]).squeeze(-1)     # [B]


# ─────────────────────────────────────────────────────────────────────────────
# Construction de la matrice de features
# ─────────────────────────────────────────────────────────────────────────────
def _build_feature_matrix(df: pd.DataFrame, behavior_map: dict) -> np.ndarray:
    """Construit la matrice [N, 10] depuis un DataFrame de clients scorés.

    Accepte les noms de colonnes en majuscules (pipeline) ou minuscules (DB).
    """
    n = len(df)
    feat = np.zeros((n, N_FEATURES), dtype=np.float32)

    def _col(upper, lower):
        if upper in df.columns:
            return pd.to_numeric(df[upper], errors="coerce").fillna(0).values
        return pd.to_numeric(df.get(lower, 0), errors="coerce").fillna(0).values

    feat[:, 0] = np.clip(_col("Montant_norm",    "montant_norm"),    0, 1)
    feat[:, 1] = np.clip(_col("Anciennete_norm", "anciennete_norm"), 0, 1)
    feat[:, 2] = np.clip(_col("Historique_norm", "historique_norm"), 0, 1)
    feat[:, 3] = np.clip(_col("Arrieres_norm",   "arrieres_norm"),   0, 1)

    # Coefficient type → 0 (Domestique) ou 1 (Entreprise)
    if "type_client" in df.columns:
        feat[:, 4] = (df["type_client"].astype(str) == "Entreprise").astype(np.float32).values
    elif "Coefficient_type" in df.columns:
        feat[:, 4] = np.where(_col("Coefficient_type", "coefficient_type") > 1.0, 1.0, 0.0)

    feat[:, 5] = np.clip(
        _col("jours_impaye", "jours_impaye") / 365.0, 0, 1
    )

    # Features comportementales depuis ClientBehavior
    refs = df["reference_abonnement"].values
    for i, ref in enumerate(refs):
        beh = behavior_map.get(ref)
        if beh:
            nb_seen = max(1, int(beh.get("nb_imports_seen") or 1))
            feat[i, 6] = float(beh.get("behavior_score")     or 50) / 100.0
            feat[i, 7] = float(beh.get("payment_freq_score") or 50) / 100.0
            feat[i, 8] = min(1.0, int(beh.get("nb_code_1")   or 0) / nb_seen)
            feat[i, 9] = min(1.0, int(beh.get("nb_payments") or 0) / nb_seen)
        else:
            feat[i, 6] = 0.5   # inconnu → neutre
            feat[i, 7] = 0.5
            feat[i, 8] = 0.5
            feat[i, 9] = 0.0

    return feat


# ─────────────────────────────────────────────────────────────────────────────
# Extraction des données d'entraînement
# ─────────────────────────────────────────────────────────────────────────────
def extract_training_data(label_window: int = LABEL_WINDOW) -> tuple[np.ndarray, np.ndarray]:
    """Extrait features + labels depuis les imports historiques.

    Pour chaque import_j :
      - Features : clients en code_relance='1' dans import_j
      - Label    : 1 si PAYMENT_CERTAIN/LIKELY dans les label_window imports suivants, sinon 0
    """
    from apps.clients.models import Client
    from apps.imports.models import FabImport
    from apps.recouvrement.models import ClientBehavior, ClientMovement

    if label_window < 1:
        raise ValueError("label_window doit etre >= 1.")

    logger.info("Chargement des %d profils ClientBehavior...", ClientBehavior.objects.count())
    behavior_map: dict = {
        row["reference_abonnement"]: row
        for row in ClientBehavior.objects.values(
            "reference_abonnement", "behavior_score", "payment_freq_score",
            "nb_code_1", "nb_payments", "nb_imports_seen",
        )
    }

    imports = list(
        FabImport.objects.filter(status=FabImport.Status.DONE)
        .order_by("file_date")
    )
    if len(imports) < label_window + 1:
        raise ValueError(f"Il faut au moins {label_window + 1} imports DONE pour extraire les données.")

    all_X, all_y = [], []
    total_paid = 0

    for idx in range(len(imports) - label_window):
        imp_from       = imports[idx]
        future_imports = imports[idx + 1 : idx + 1 + label_window]

        # Clients scorés (code_relance='1') dans imp_from avec features calculées
        rows = list(
            Client.objects.filter(
                import_ref=imp_from,
                code_relance="1",
                montant_norm__isnull=False,
            ).values(
                "reference_abonnement",
                "montant_norm", "anciennete_norm", "historique_norm",
                "arrieres_norm", "coefficient_type", "type_client",
                "jours_impaye",
            )
        )
        if not rows:
            continue

        df_c = pd.DataFrame(rows)

        # Labels : a payé dans un des 7 imports suivants
        paid_refs = set(
            ClientMovement.objects.filter(
                reference_abonnement__in=df_c["reference_abonnement"].tolist(),
                import_to__in=future_imports,
                type__in=["payment_certain", "payment_likely"],
            ).values_list("reference_abonnement", flat=True)
        )

        feat   = _build_feature_matrix(df_c, behavior_map)
        labels = np.array(
            [1.0 if r in paid_refs else 0.0 for r in df_c["reference_abonnement"]],
            dtype=np.float32,
        )

        all_X.append(feat)
        all_y.append(labels)
        total_paid += int(labels.sum())

        if (idx + 1) % 20 == 0:
            logger.info(
                "  %d/%d imports traités — %d exemples cumulés",
                idx + 1, len(imports) - label_window, sum(len(a) for a in all_y),
            )

    if not all_X:
        raise ValueError("Aucune donnée d'entraînement extraite.")

    X = np.vstack(all_X)
    y = np.concatenate(all_y)
    logger.info(
        "Dataset final : %d exemples | %.1f%% paiements",
        len(y), y.mean() * 100,
    )
    return X, y


# ─────────────────────────────────────────────────────────────────────────────
# Entraînement
# ─────────────────────────────────────────────────────────────────────────────
def _auc_roc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """AUC-ROC par méthode trapézoïdale (sans dépendance sklearn)."""
    n_pos = y_true.sum()
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    desc = np.argsort(-y_score)
    y_sorted = y_true[desc]
    tpr = np.concatenate([[0], np.cumsum(y_sorted) / n_pos])
    fpr = np.concatenate([[0], np.cumsum(1 - y_sorted) / n_neg])
    return float(np.trapz(tpr, fpr))


def train_model(label_window: int = LABEL_WINDOW) -> dict:
    """Entraîne le FT-Transformer et sauvegarde le meilleur modèle.

    Retourne un dict de métriques finales.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Début entraînement FT-Transformer sur %s", device)

    X, y = extract_training_data(label_window=label_window)

    # Split 80 / 20
    n     = len(X)
    perm  = np.random.permutation(n)
    split = int(0.8 * n)
    X_tr, X_val = X[perm[:split]], X[perm[split:]]
    y_tr, y_val = y[perm[:split]], y[perm[split:]]

    X_tr_t  = torch.from_numpy(X_tr).to(device)
    y_tr_t  = torch.from_numpy(y_tr).to(device)
    X_val_t = torch.from_numpy(X_val).to(device)
    y_val_t = torch.from_numpy(y_val).to(device)

    # Pondération pour déséquilibre de classes (peu de paiements)
    pos_w = torch.tensor(
        [(y_tr == 0).sum() / max(1, (y_tr == 1).sum())],
        device=device,
    )
    logger.info("Poids classe positive : %.2f", pos_w.item())

    model     = _FTTransformer().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=N_EPOCHS)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_w)

    best_val_loss  = float("inf")
    patience_count = 0
    best_metrics: dict = {}

    for epoch in range(1, N_EPOCHS + 1):
        # ── Train ──
        model.train()
        perm_e     = torch.randperm(len(X_tr_t), device=device)
        epoch_loss = 0.0
        n_batches  = 0

        for start in range(0, len(X_tr_t), BATCH_SIZE):
            idx_b = perm_e[start : start + BATCH_SIZE]
            xb, yb = X_tr_t[idx_b], y_tr_t[idx_b]
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item()
            n_batches  += 1

        scheduler.step()

        # ── Validation ──
        model.eval()
        with torch.no_grad():
            val_logits = model(X_val_t)
            val_loss   = criterion(val_logits, y_val_t).item()
            val_probs  = torch.sigmoid(val_logits).cpu().numpy()

        val_auc = _auc_roc(y_val, val_probs)

        logger.info(
            "Epoch %2d/%d | loss=%.4f val_loss=%.4f val_auc=%.3f",
            epoch, N_EPOCHS, epoch_loss / n_batches, val_loss, val_auc,
        )

        if val_loss < best_val_loss:
            best_val_loss  = val_loss
            patience_count = 0
            best_metrics   = {"val_loss": val_loss, "val_auc": val_auc, "epoch": epoch}
            _save_checkpoint(model)
        else:
            patience_count += 1
            if patience_count >= PATIENCE:
                logger.info("Early stopping à l'epoch %d", epoch)
                break

    logger.info("Entraînement terminé. Meilleur : %s", best_metrics)
    return best_metrics


# ─────────────────────────────────────────────────────────────────────────────
# Sauvegarde / Chargement
# ─────────────────────────────────────────────────────────────────────────────
def _save_checkpoint(model: _FTTransformer) -> None:
    torch.save(
        {
            "state_dict": model.state_dict(),
            "n_features": N_FEATURES,
            "d_token":    D_TOKEN,
            "n_heads":    N_HEADS,
            "n_layers":   N_LAYERS,
        },
        _model_path(),
    )
    logger.info("Modèle sauvegardé : %s", _model_path())


_cached_model: _FTTransformer | None = None


def _get_model() -> _FTTransformer | None:
    """Charge le modèle en mémoire (lazy, une seule fois)."""
    global _cached_model
    if _cached_model is not None:
        return _cached_model

    path = _model_path()
    if not path.exists():
        logger.warning(
            "ft_transformer_snde.pt introuvable — scoring par formule utilisé. "
            "Lancez : python manage.py train_ft_transformer"
        )
        return None

    ckpt  = torch.load(path, map_location="cpu", weights_only=True)
    model = _FTTransformer(
        n_features=ckpt["n_features"],
        d_token=ckpt["d_token"],
        n_heads=ckpt["n_heads"],
        n_layers=ckpt["n_layers"],
    )
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    _cached_model = model
    logger.info("FT-Transformer chargé depuis %s", path)
    return model


# ─────────────────────────────────────────────────────────────────────────────
# Inférence — appelée depuis pipeline.py
# ─────────────────────────────────────────────────────────────────────────────
def predict_scores(df: pd.DataFrame) -> pd.Series:
    """Remplace le Score formule par la probabilité de paiement IA [0, 1].

    Appelée automatiquement depuis compute_score_components() quand
    SCORING_ENGINE=ft_transformer dans .env.

    Si le modèle n'est pas entraîné → fallback silencieux sur le Score formule.
    """
    model = _get_model()
    if model is None:
        return df["Score"]  # fallback formule

    from apps.recouvrement.models import ClientBehavior

    refs = df["reference_abonnement"].tolist()
    behavior_map = {
        row["reference_abonnement"]: row
        for row in ClientBehavior.objects.filter(
            reference_abonnement__in=refs
        ).values(
            "reference_abonnement", "behavior_score", "payment_freq_score",
            "nb_code_1", "nb_payments", "nb_imports_seen",
        )
    }

    feat = _build_feature_matrix(df, behavior_map)
    X    = torch.from_numpy(feat)

    with torch.no_grad():
        probs = torch.sigmoid(model(X)).numpy()

    return pd.Series(probs, index=df.index, name="Score")
