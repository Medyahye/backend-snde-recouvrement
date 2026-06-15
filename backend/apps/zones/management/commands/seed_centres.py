"""Peuple la table Centre avec les 91 entrées de référence SNDE.

Source : table de mapping du notebook `Reminders_Report_Colab.ipynb`
(cellules 15/16). Idempotent : exécutable plusieurs fois sans doublon,
met à jour le nom si différent.
"""
from django.core.management.base import BaseCommand

from apps.zones.models import Centre

# Table de correspondance code (str) → nom du centre.
# Reproduit exactement le dict CENTRES du notebook.
CENTRES_DATA: dict[str, str] = {
    "14": "BENICHAB",
    "15": "CHAMI",
    "16": "AOUJEFT",
    "17": "CHINGHITI",
    "18": "OUADANE",
    "19": "MOUDJERIA",
    "20": "TICHIT",
    "21": "FDEIRICK",
    "22": "BIR MOGHREIN",
    "23": "RKIZ",
    "24": "KEUR MACENE",
    "25": "BABABE",
    "26": "M'BAGNE",
    "27": "MAGHAMA",
    "28": "MONGUEL",
    "29": "OULD YENGE",
    "30": "BARKEOL",
    "31": "BOUMDEID",
    "32": "TAMCHAKET",
    "33": "OULATA",
    "34": "AMOURJ",
    "35": "ADEL BAGROU",
    "36": "SANGRAVA",
    "37": "VASALA",
    "38": "TERMISSE",
    "39": "KIFFA2",
    "40": "NOUADHIBOU 3",
    "41": "TEYARETT2",
    "42": "CARREFOUR2",
    "43": "TEVRAGHZEINA-SUD",
    "44": "DAR NAIM-NORD",
    "45": "MELLAH",
    "46": "MALLAH 2",
    "47": "TEYARETT",
    "48": "DAR NAIM SUD 2",
    "49": "TARHIL1",
    "50": "TARHIL2",
    "51": "TARHIL3",
    "52": "MALLAH 3",
    "53": "RIYAD2",
    "54": "DIR. TECHNICO-COMMERC.",
    "55": "TEVRAGHZEINA-NORD 3",
    "60": "NOUADHIBOU 1",
    "61": "BOGHE",
    "62": "AKJOUJT",
    "63": "Khadamaty",
    "64": "ROSSO",
    "65": "KAEDI",
    "66": "ATAR",
    "67": "ALEG",
    "68": "BOUTILIMIT",
    "69": "MEDERDRA",
    "70": "AIOUN EL ATROUSS",
    "71": "NEMA",
    "72": "TIMBEDRA",
    "73": "KIFFA1",
    "74": "GUEROU",
    "75": "SELIBABY",
    "76": "M'BOUT",
    "77": "MAGTALAHAJAR",
    "78": "TIDJIKJA",
    "79": "TINTANE ANCIEN",
    "80": "NOUADHIBOU 2",
    "81": "DJIGUENI",
    "82": "KANKOUSSA",
    "83": "KOUBENNI",
    "84": "BASSIKNOU",
    "85": "TINTANE NOUVEAU",
    "86": "WAD NAGA",
    "87": "CHOGGAR",
    "88": "N'BEIKETTE LEHWACH",
    "89": "RIYAD1",
    "90": "ARAFAT",
    "91": "TEVRAGHZEINA-NORD 1",
    "92": "CAPITALE",
    "93": "SEBKHA",
    "94": "EL MINA",
    "95": "CARREFOUR1",
    "96": "KSAR",
    "97": "TEVRAGH ZEINA NORD 2",
    "98": "TOUJOUNINE",
    "99": "DAR NAIM-SUD 1",
    "101": "AIWNAT ZBEL",
    "102": "TOUIL",
    "103": "BOUHDIDA",
    "104": "MAAL",
    "105": "LEXEIBA",
    "106": "WOMPOU",
    "107": "GABOU",
    "108": "TEKANE",
    "109": "CHINGUITT NOUVEAU",
}


class Command(BaseCommand):
    help = "Peuple la table Centre avec les 91 entrées de référence SNDE."

    def handle(self, *args, **options):
        created, updated, unchanged = 0, 0, 0

        for code, nom in CENTRES_DATA.items():
            obj, was_created = Centre.objects.get_or_create(
                code=code, defaults={"nom": nom}
            )
            if was_created:
                created += 1
            elif obj.nom != nom:
                obj.nom = nom
                obj.save(update_fields=["nom"])
                updated += 1
            else:
                unchanged += 1

        total = Centre.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Seed terminé : {created} créés, {updated} mis à jour, "
                f"{unchanged} inchangés. Total en base : {total}."
            )
        )
