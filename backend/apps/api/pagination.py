"""Pagination personnalisée.

DRF par défaut ignore le paramètre `?page_size=` côté client : la taille de
page est figée à `PAGE_SIZE` dans settings.py. Cette classe permet au
frontend de demander une page plus grande (utile pour les selectors qui
veulent l'intégralité des imports, des zones, etc.).

Plafond fixé à 500 pour éviter d'abuser (un dropdown au-delà devient
inutilisable de toute façon).
"""
from rest_framework.pagination import PageNumberPagination


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 50                  # défaut si pas de param
    page_size_query_param = "page_size"
    max_page_size = 500             # plafond demandable par le client
