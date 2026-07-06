"""
pptx_to_pdf.py - Conversion PPTX -> PDF via PowerPoint COM, en PROCESSUS ISOLÉ.

Exécuté en sous-processus (python -m / subprocess) pour éviter toute instabilité
COM liée aux threads du serveur (une instance PowerPoint fraîche par appel).

Usage : python pptx_to_pdf.py <entree.pptx> <sortie.pdf>
Codes retour : 0 = OK ; 2 = erreur COM/PowerPoint.
"""

import os
import sys


def convertir(pptx_path, pdf_path):
    import pythoncom
    import win32com.client as win32

    pythoncom.CoInitialize()
    ppt = None
    pres = None
    try:
        # DispatchEx = nouvelle instance dédiée (pas de réutilisation d'une
        # instance restée ouverte, source d'erreurs "Presentations").
        ppt = win32.DispatchEx("PowerPoint.Application")
        pres = ppt.Presentations.Open(os.path.abspath(pptx_path),
                                      ReadOnly=True, WithWindow=False)
        pres.SaveAs(os.path.abspath(pdf_path), 32)  # 32 = ppSaveAsPDF
        pres.Close()
        pres = None
        return 0
    finally:
        try:
            if pres is not None:
                pres.Close()
        except Exception:
            pass
        try:
            if ppt is not None:
                ppt.Quit()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _tuer_powerpoint_residuels():
    """Tue les instances PowerPoint fantômes (COM figé d'un run précédent)."""
    os.system("taskkill /F /IM POWERPNT.EXE >nul 2>&1")


if __name__ == "__main__":
    import time
    if len(sys.argv) < 3:
        print("usage: pptx_to_pdf.py <in.pptx> <out.pdf>", file=sys.stderr)
        sys.exit(1)
    pptx, pdf = sys.argv[1], sys.argv[2]
    erreur = None
    for essai in (1, 2, 3):
        try:
            if os.path.exists(pdf):
                os.remove(pdf)
            convertir(pptx, pdf)
            if os.path.exists(pdf):
                print("OK")
                sys.exit(0)
            erreur = "SaveAs sans fichier"
        except Exception as e:
            erreur = str(e)
        # échec : on nettoie les PowerPoint figés avant de réessayer
        _tuer_powerpoint_residuels()
        time.sleep(1.5)
    print("ERREUR COM:", erreur, file=sys.stderr)
    print("ERR")
    sys.exit(2)
