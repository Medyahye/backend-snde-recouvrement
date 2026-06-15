"""Parser FAB — port fidèle de la cellule 11 du notebook.

Format FAB : texte brut, séparateur `$$`, ≥35 colonnes par ligne.
Le parser extrait uniquement les colonnes utiles à la V1 + le calcul
`arrieres = solde - montant_facture`.

Encodage : les FABs SNDE peuvent arriver en UTF-8 (manuel) ou UTF-16
(export S3 depuis systèmes Windows/Oracle). `decode_fab_bytes()` détecte
automatiquement et retourne du texte propre.
"""
import re
from datetime import date, datetime
from typing import Tuple

import pandas as pd

REMINDER_RE = re.compile(r"\$\$([0-4])$")


def decode_fab_bytes(content: bytes) -> str:
    """Décode automatiquement un FAB selon son encodage réel.

    Ordre d'essai :
      1. BOM UTF-8 (`\\xef\\xbb\\xbf`) → utf-8-sig
      2. BOM UTF-16 LE/BE (`\\xff\\xfe` / `\\xfe\\xff`) → utf-16
      3. UTF-8 strict — si décode propre, on garde
      4. UTF-16 LE sans BOM (cas typique exports Windows SNDE)
      5. Windows-1252 (Latin-1 étendu)
      6. Dernier recours : utf-8 avec `errors=ignore`

    Heuristique pour éliminer un faux positif UTF-8 (UTF-16 décodé en
    UTF-8 donne plein de `\\x00` invisibles) : on rejette si > 10% du
    début est composé de bytes null.
    """
    if not content:
        return ""

    # 1. BOMs explicites
    if content.startswith(b"\xef\xbb\xbf"):
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    if content.startswith(b"\xff\xfe") or content.startswith(b"\xfe\xff"):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError:
            pass

    # 2. UTF-8 strict — accepté seulement si pas de bytes null dans l'échantillon
    sample = content[:2000]
    null_ratio = sample.count(b"\x00") / max(1, len(sample))
    try:
        text = content.decode("utf-8")
        if null_ratio < 0.1:
            return text
    except UnicodeDecodeError:
        pass

    # 3. UTF-16 LE sans BOM (très fréquent côté SNDE / Oracle exports)
    if null_ratio >= 0.1:
        try:
            text = content.decode("utf-16-le")
            return text
        except UnicodeDecodeError:
            pass
        try:
            text = content.decode("utf-16-be")
            return text
        except UnicodeDecodeError:
            pass

    # 4. Windows-1252
    try:
        return content.decode("cp1252")
    except UnicodeDecodeError:
        pass

    # 5. Dernier recours
    return content.decode("utf-8", errors="ignore")


def _fnum(v: str) -> float:
    """Float robuste : '1,5' ou '1.5' → 1.5 ; valeur invalide → 0.0."""
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


def _fint(v: str) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def _fdate(v: str):
    """Format FAB DD/MM/YY → datetime.date. Invalide → None."""
    try:
        return datetime.strptime(v, "%d/%m/%y").date()
    except (ValueError, TypeError):
        return None


def _norm_phone(s: str) -> str:
    """Normalise un téléphone : garde uniquement les chiffres et le + initial."""
    if not s:
        return ""
    s = str(s).strip()
    keep_plus = s.startswith("+")
    digits = "".join(c for c in s if c.isdigit())
    return ("+" + digits) if (keep_plus and digits) else digits


def parse_fab_text(content: str, statement_date: date) -> Tuple[pd.DataFrame, dict]:
    """Parse un FAB textuel et retourne (DataFrame, stats).

    Gère 2 formats de FAB observés en production :
      - **35 colonnes** : format avec `gps_x` / `gps_y` (parts 30-31)
        → `code_echeance` = parts[32], `code_relance` = parts[34]
      - **33 colonnes** : format sans GPS (historique S3)
        → `code_echeance` = parts[30], `code_relance` = parts[32]

    stats = {
        "total", "valid", "invalid",
        "reminder_counts", "format_detected", "format_counts",
    }
    """
    rows: list[dict] = []
    invalid = 0
    counts = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "err": 0}
    format_counts = {"33": 0, "34": 0, "35": 0}

    for line in content.splitlines():
        line = line.strip()
        if not line:
            invalid += 1
            continue
        parts = line.split("$$")

        # Détection du format SNDE par nombre de colonnes :
        #   33 cols (le + ancien, Oct-Nov 2025) : ni NNI, ni nb_paiements_par_jour
        #   34 cols (intermédiaire, Déc 2025 - mi-Janv 2026) : nb_paiements_par_jour
        #     ajouté à l'index 25 (mais NNI toujours absent)
        #   35 cols (actuel, à partir de mi-Janv 2026) : NNI ajouté à l'index 11
        #     en plus de nb_paiements_par_jour (≈ format documenté SNDE)
        # Référence : doc officielle SNDE — colonnes ajoutées au milieu, pas
        # à la fin, donc on doit shifter manuellement les indexes selon format.
        ncols = len(parts)
        if ncols >= 35:
            idx_nni = 11
            idx_numero_compteur = 12
            idx_activite_client = 19
            idx_solde = 22
            idx_date_facture = 23
            idx_montant_facture = 24
            idx_date_dernier_paiement = 25
            idx_releveur_1 = 27
            idx_code_echeance = 32
            idx_code_relance = 34
            format_counts["35"] += 1
        elif ncols == 34:
            # nb_paiements ajouté (idx 25), NNI pas encore
            # → de 26 à la fin, tout est shifté de +1 vs 33-col
            idx_nni = None
            idx_numero_compteur = 11
            idx_activite_client = 18
            idx_solde = 21
            idx_date_facture = 22
            idx_montant_facture = 23
            idx_date_dernier_paiement = 24
            idx_releveur_1 = 26
            idx_code_echeance = 31
            idx_code_relance = 33
            format_counts["34"] += 1
        elif ncols >= 33:
            idx_nni = None
            idx_numero_compteur = 11
            idx_activite_client = 18
            idx_solde = 21
            idx_date_facture = 22
            idx_montant_facture = 23
            idx_date_dernier_paiement = 24
            idx_releveur_1 = 25
            idx_code_echeance = 30
            idx_code_relance = 32
            format_counts["33"] += 1
        else:
            invalid += 1
            continue

        m = REMINDER_RE.search(line)
        counts[m.group(1) if m else "err"] += 1

        rows.append(
            {
                "code_activite": parts[0].strip(),
                "annee": _fint(parts[1]),
                "mois": _fint(parts[2]),
                "code_centre": parts[3].strip(),
                "secteur_facturation": parts[4].strip(),
                "tournee_releve": parts[5].strip(),
                "reference_abonnement": parts[6].strip(),
                "nom_client": parts[7].strip(),
                "adresse": parts[8].strip(),
                "telephone": _norm_phone(parts[9]),
                "whatsapp": _norm_phone(parts[10]),
                "nni": parts[idx_nni].strip() if idx_nni is not None else "",
                "numero_compteur": parts[idx_numero_compteur].strip(),
                "activite_client": parts[idx_activite_client].strip(),
                "solde": _fnum(parts[idx_solde]),
                "date_facture": _fdate(parts[idx_date_facture]),
                "montant_facture": _fnum(parts[idx_montant_facture]),
                "date_dernier_paiement": _fdate(parts[idx_date_dernier_paiement]),
                "releveur_1": parts[idx_releveur_1].strip()
                if ncols > idx_releveur_1
                else "",
                "code_echeance": parts[idx_code_echeance].strip()
                if ncols > idx_code_echeance
                else "",
                "code_relance": parts[idx_code_relance].strip()
                if ncols > idx_code_relance
                else "",
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df["arrieres"] = df["solde"] - df["montant_facture"]
        df["date_releve"] = statement_date

    # Détermine le format dominant
    nb_formats_detectes = sum(1 for v in format_counts.values() if v > 0)
    if nb_formats_detectes > 1:
        format_label = "mixed"
    elif format_counts["35"] > 0:
        format_label = "35-cols"
    elif format_counts["34"] > 0:
        format_label = "34-cols"
    elif format_counts["33"] > 0:
        format_label = "33-cols"
    else:
        format_label = "unknown"

    stats = {
        "total": len(rows) + invalid,
        "valid": len(rows),
        "invalid": invalid,
        "reminder_counts": counts,
        "format_detected": format_label,
        "format_counts": format_counts,
    }
    return df, stats
