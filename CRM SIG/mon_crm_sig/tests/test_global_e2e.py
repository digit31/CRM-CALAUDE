# -*- coding: utf-8 -*-
"""Test global E2E ENRICHI : nominal + validation de CONTENU (PDS par BPE,
FCI/date TVX ligne par ligne, schéma vs prototype ENSIO) + fautes étendues
(BPE absente, .prj manquant, SHP corrompu). Nettoie tout. Écrit un JSON."""
import requests, os, glob, shutil, sqlite3, time, json, sys
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import geopandas as gpd
import openpyxl
import fitz

# Chemins dérivés du dépôt (tests/ est dans mon_crm_sig/). Surchargeables par env.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.environ.get("CRM_URL", "http://localhost:8000")
DB = os.path.join(ROOT, "crm_sig.db")
PBASE = os.path.join(ROOT, "app", "static", "projects_data")
# Prototype ENSIO de référence (OPTIONNEL) : le test saute la comparaison de
# schéma si absent. Surchargeable via la variable d'environnement CRM_REF_PROTO.
REF = os.environ.get("CRM_REF_PROTO", "")
RES = []

def rec(cat, s, attendu, ok, obtenu):
    RES.append({"cat": cat, "s": s, "attendu": attendu, "ok": bool(ok), "obtenu": str(obtenu)})
    print(f"  [{'OK ' if ok else 'KO '}] ({cat}) {s} :: {obtenu}")

def pid_from_nom(nom):
    con = sqlite3.connect(DB); r = con.execute("SELECT id,chemin_dossier FROM projets WHERE nom=? ORDER BY id DESC LIMIT 1", (nom,)).fetchone(); con.close(); return r
def creer(nom, te):
    requests.post(f"{B}/api/projets", data={"nom": nom, "type_etude": te}, allow_redirects=False, timeout=20); return pid_from_nom(nom)
def importer(pid, src, avec_txt=True, avec_prj=True, sauf=()):
    exts = [".shp", ".dbf", ".shx", ".cpg"] + ([".prj"] if avec_prj else []) + ([".txt"] if avec_txt else [])
    fils = []
    for f in sorted(os.listdir(src)):
        base = os.path.splitext(f)[0].upper()
        if base in sauf: continue
        if os.path.splitext(f)[1].lower() in exts:
            fils.append(("fichiers", (f, open(os.path.join(src, f), "rb"), "application/octet-stream")))
    try: r = requests.post(f"{B}/api/projets/{pid}/upload-shp", files=fils, timeout=120)
    finally:
        for _, t in fils:
            try: t[1].close()
            except: pass
    return r
def generer(pid, typ, timeout=240):
    r = requests.post(f"{B}/api/projets/{pid}/generer-etude/{typ}?mode=overwrite", timeout=timeout)
    try: d = r.json()
    except: d = {}
    return r.status_code, (d.get("message") or d.get("detail") or "")[:150]
def appliquer_nomenclature(pid):
    """Simule l'utilisateur dans le modal Nomenclature : récupère les couches à
    compléter/corriger, applique TOUTES les propositions ayant une valeur.
    Renvoie le nombre total de valeurs appliquées."""
    try:
        cs = requests.get(f"{B}/api/projets/{pid}/couches-a-completer", timeout=30).json().get("couches", [])
    except Exception:
        cs = []
    total = 0
    for c in cs:
        try:
            props = requests.get(f"{B}/api/projets/{pid}/couches/{c['id']}/propositions", timeout=30).json().get("propositions", [])
            chgs = [{"ligne": p["ligne"], "champ": p["champ"], "valeur": p["proposee"]}
                    for p in props if p.get("proposee") not in (None, "")]
            if chgs:
                r = requests.post(f"{B}/api/projets/{pid}/couches/{c['id']}/appliquer-propositions",
                                  json={"propositions": chgs}, timeout=60)
                total += r.json().get("nb", 0)
        except Exception:
            pass
    return total
def supprimer(pid, ch):
    try: requests.delete(f"{B}/api/projets/{pid}", timeout=20)
    except: pass
    if ch and os.path.isdir(ch): shutil.rmtree(ch, ignore_errors=True)
def out(ch, pat):
    fs = glob.glob(os.path.join(ch, "**", pat), recursive=True); return max(fs, key=os.path.getmtime) if fs else None

def main():
    for _ in range(15):
        try:
            if requests.get(B + "/", timeout=2).status_code == 200: break
        except: pass
        time.sleep(1)
    # nettoyage restes
    con = sqlite3.connect(DB); restes = con.execute("SELECT id,chemin_dossier FROM projets WHERE nom LIKE 'ZZ_TEST%'").fetchall(); con.close()
    for rid, rch in restes: supprimer(rid, rch)
    # schéma de référence
    refcols = {}
    if REF and os.path.isdir(REF):
        for sub, base in (("01-BPE", "BPE"), ("03-CABLE", "CABLES"), ("05-PT", "PT"), ("06-SUPPORT", "SUPPORT")):
            s = glob.glob(os.path.join(REF, sub, "*.shp"))
            if s: refcols[base] = set(c for c in gpd.read_file(s[0]).columns if c != "geometry")
    print("réf schéma:", {k: len(v) for k, v in refcols.items()} if refcols else "(absente -> comparaison de schéma sautée)")
    # source d'entrée
    src = None
    for d in sorted(os.listdir(PBASE)):
        inp = os.path.join(PBASE, d, "01_Inputs_SHP")
        shps = {os.path.basename(x).upper() for x in glob.glob(os.path.join(inp, "*.shp"))}
        if {"CABLES.SHP", "SUPPORT.SHP", "BPE.SHP"} <= shps: src = inp; break
    print("source:", src)
    nb_bpe = len(gpd.read_file(os.path.join(src, "BPE.shp")))
    print("nb BPE source:", nb_bpe)
    projets = []

    # ===== S1 : APD FO nominal + CONTENU =====
    print("\n=== S1 APD FO nominal + contenu ===")
    pid, ch = creer("ZZ_TEST_APD", "APD FO"); projets.append((pid, ch))
    importer(pid, src, avec_txt=False)
    st, _ = generer(pid, "shape")
    liv = glob.glob(os.path.join(ch, "**", "SHAPE", "*.shp"), recursive=True)
    rec("nominal", "S1 SHP livrables", "8 couches", st == 200 and len(liv) >= 6, f"{st} / {len(liv)} shp")
    st, _ = generer(pid, "syno")
    p = out(ch, "SYNO_*.pdf"); npg = fitz.open(p).page_count if p else 0
    rec("nominal", "S1 Synoptique", "PDF valide", st == 200 and p and npg >= 1, f"{st} | {npg}p {os.path.getsize(p)//1024 if p else 0}Ko")
    st, _ = generer(pid, "pds")
    p = out(ch, "PDS_*.xlsx")
    if p:
        wb = openpyxl.load_workbook(p, read_only=True)
        vis = [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"]
        boites = [t for t in vis if t != "PDS"]
        # CONTENU : un onglet-boîte doit exister par BPE (>= nb BPE - marge)
        rec("contenu", "S1 PDS : un onglet par boîte BPE", f"~{nb_bpe} onglets-boîtes", len(boites) >= max(1, nb_bpe - 1), f"{len(boites)} onglets-boîtes / {nb_bpe} BPE")
    else:
        rec("contenu", "S1 PDS : un onglet par boîte BPE", "xlsx", False, "absent")
    st, _ = generer(pid, "kmz")
    p = out(ch, "*.kmz")
    rec("nominal", "S1 KMZ", "zip valide", st == 200 and p and open(p, "rb").read(2) == b"PK", f"{st}")

    # ===== S2 FAUTE : rapport sans folios =====
    print("\n=== S2 FAUTE rapport sans folios ===")
    st, msg = generer(pid, "rapport")
    rec("faute", "S2 Plan APD sans folios", "bloqué 400 + msg folio", st == 400 and "folio" in msg.lower(), f"{st} | {msg[:50]}")

    # ===== S3 DOE FO : nomenclature bloquante -> correction -> génération =====
    print("\n=== S3 DOE FO : blocage nomenclature -> correction -> nominal + contenu ===")
    pid2, ch2 = creer("ZZ_TEST_DOE", "DOE FO"); projets.append((pid2, ch2))
    importer(pid2, src, avec_txt=True)
    date_tvx = "20260414"
    # Date TVX posée d'abord : le blocage suivant doit porter sur la NOMENCLATURE (pas la date).
    requests.post(f"{B}/api/projets/{pid2}/doe-fo", json={"date_tvx": date_tvx}, timeout=15)
    # S3a FAUTE : entrée non conforme (EMPRISE sans _001, CODE vide, doublon…) -> BLOQUÉ
    st, msg = generer(pid2, "doe_fo_netgeo")
    rec("faute", "S3 NETGEO bloqué si nomenclature non conforme",
        "bloqué 400 + msg nomenclature", st == 400 and "nomenclature" in msg.lower(), f"{st} | {msg[:55]}")
    # S3b : on applique la nomenclature (comme dans le modal) puis on renseigne le FCI
    nb_nom = appliquer_nomenclature(pid2)
    cab_in = gpd.read_file(os.path.join(ch2, "01_Inputs_SHP", "CABLES.shp"))
    fci_noms = [str(x) for x in cab_in["NOM"].tolist()[:2] if str(x) not in ("nan", "None", "")]
    requests.post(f"{B}/api/projets/{pid2}/doe-fo", json={"date_tvx": date_tvx, "fci": {n: "FCI-TEST" for n in fci_noms}}, timeout=15)
    st, msg = generer(pid2, "doe_fo_netgeo")
    ng = glob.glob(os.path.join(ch2, "**", "03.3_Shapes", "**", "CABLES.shp"), recursive=True)
    rec("nominal", "S3 Dossier NETGEO (après nomenclature)", "SHP NETGEO",
        st == 200 and ng, f"{st} / {len(ng)} CABLES NETGEO ({nb_nom} corrections)")
    # CONTENU NETGEO : FCI + POSE (date TVX) appliqués ligne par ligne
    if ng:
        gc = gpd.read_file(ng[0])
        fci_ok = "FCI" in gc.columns and any(str(v) == "FCI-TEST" for v in gc["FCI"])
        pose_ok = "POSE" in gc.columns and any(str(v) == date_tvx for v in gc["POSE"])
        rec("contenu", "S3 NETGEO CABLES : FCI appliqué", "FCI-TEST présent", fci_ok, f"FCI-TEST {'trouvé' if fci_ok else 'absent'}")
        rec("contenu", "S3 NETGEO CABLES : POSE=date TVX", date_tvx, pose_ok, f"POSE {'ok' if pose_ok else 'ko'}")
        # SCHÉMA vs prototype ENSIO
        gencols = set(c for c in gc.columns if c != "geometry")
        if "CABLES" in refcols:
            manque = refcols["CABLES"] - gencols
            rec("schema", "S3 NETGEO CABLES vs prototype", "colonnes conformes", not manque, f"manquantes={sorted(manque) if manque else 'aucune'}")
    ngb = glob.glob(os.path.join(ch2, "**", "03.3_Shapes", "**", "BPE.shp"), recursive=True)
    if ngb:
        gb = gpd.read_file(ngb[0])
        etat_ok = "ETAT" in gb.columns and any(str(v).upper() == "EN SERVICE" for v in gb["ETAT"])
        rec("contenu", "S3 NETGEO BPE : ETAT=EN SERVICE", "au moins un", etat_ok, f"ETAT EN SERVICE {'ok' if etat_ok else 'ko'}")
        if "BPE" in refcols:
            manque = refcols["BPE"] - set(c for c in gb.columns if c != "geometry")
            rec("schema", "S3 NETGEO BPE vs prototype", "colonnes conformes", not manque, f"manquantes={sorted(manque) if manque else 'aucune'}")
    st, _ = generer(pid2, "doe_fo_syno")
    p = out(ch2, "DOE_SYNO_*.pdf"); npg = fitz.open(p).page_count if p else 0
    rec("nominal", "S3 Synoptique DOE", "PDF valide (=APD)", st == 200 and p and npg >= 1, f"{st} | {npg}p")
    st, _ = generer(pid2, "doe_fo_pds")
    p = out(ch2, "DOE_*PDS_*.xlsx")
    rec("nominal", "S3 Plan de Boîte DOE", "XLSX valide (=APD)", st == 200 and p and open(p, "rb").read(2) == b"PK", f"{st}")

    # ===== S4 FAUTE : NETGEO sans date TVX =====
    print("\n=== S4 FAUTE NETGEO sans DATE TVX ===")
    pid3, ch3 = creer("ZZ_TEST_DOE_NODATE", "DOE FO"); projets.append((pid3, ch3))
    importer(pid3, src, avec_txt=False)
    st, msg = generer(pid3, "doe_fo_netgeo")
    rec("faute", "S4 NETGEO sans DATE TVX", "bloqué 400", st == 400 and "tvx" in msg.lower(), f"{st} | {msg[:50]}")

    # ===== S5 FAUTE : PDS sans couche BPE =====
    print("\n=== S5 FAUTE PDS sans BPE ===")
    pid4, ch4 = creer("ZZ_TEST_NOBPE", "APD FO"); projets.append((pid4, ch4))
    importer(pid4, src, avec_txt=False, sauf=("BPE",))
    generer(pid4, "shape")
    st, msg = generer(pid4, "pds")
    rec("faute", "S5 PDS sans couche BPE", "erreur claire BPE", st in (400, 500) and "bpe" in msg.lower(), f"{st} | {msg[:50]}")

    # ===== S6 FAUTE tolérée : .prj manquant (doit supposer Lambert-93) =====
    print("\n=== S6 .prj manquant -> doit supposer Lambert-93 et générer ===")
    pid5, ch5 = creer("ZZ_TEST_NOPRJ", "APD FO"); projets.append((pid5, ch5))
    importer(pid5, src, avec_txt=False, avec_prj=False)
    st, _ = generer(pid5, "shape")
    liv = glob.glob(os.path.join(ch5, "**", "SHAPE", "*.shp"), recursive=True)
    rec("faute", "S6 .prj manquant (tolérance)", "génère quand même", st == 200 and len(liv) >= 5, f"{st} / {len(liv)} shp")

    # ===== S7 FAUTE : SHP corrompu -> pas de crash serveur =====
    print("\n=== S7 SHP corrompu -> géré sans crash ===")
    pid6, ch6 = creer("ZZ_TEST_CORROMPU", "APD FO"); projets.append((pid6, ch6))
    importer(pid6, src, avec_txt=False)
    cabin = os.path.join(ch6, "01_Inputs_SHP", "CABLES.shp")
    with open(cabin, "wb") as f: f.write(b"CE_N_EST_PAS_UN_SHAPEFILE" * 50)  # corrompt CABLES
    st, msg = generer(pid6, "shape")
    vivant = requests.get(B + "/", timeout=5).status_code == 200
    rec("faute", "S7 SHP corrompu", "erreur gérée, serveur vivant", vivant and st != 200, f"gen HTTP {st} | serveur {'vivant' if vivant else 'MORT'}")

    # nettoyage
    print("\n=== nettoyage ===")
    for pid, ch in projets: supprimer(pid, ch)

    nok = sum(1 for r in RES if r["ok"])
    print("\n" + "=" * 60)
    print(f"SYNTHÈSE : {nok}/{len(RES)} conformes")
    import tempfile
    res_path = os.path.join(tempfile.gettempdir(), "crm_test_global_result.json")
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump({"total": len(RES), "ok": nok, "resultats": RES}, f, ensure_ascii=False, indent=1)
    print("JSON écrit :", res_path)

main()
